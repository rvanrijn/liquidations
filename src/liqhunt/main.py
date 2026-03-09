# src/liqhunt/main.py
"""Main entry point for the liquidation hunt signal engine."""

import asyncio
import logging
import logging.handlers
import subprocess
from collections import deque
from pathlib import Path
from time import time as _time

from src.liqhunt.candles import CandleFetcher
from src.liqhunt.dashboard import LiqHuntDashboard
from src.liqhunt.liq_feed import LiqFeed
from src.liqhunt.battle_trader import BattleTrader
from src.liqhunt.paper_trader import PaperTrader
from src.liqhunt.pipeline_report import update_pipeline_html
from src.liqhunt.signal_engine import SignalEngine
from src.liqlevels.client import BinanceLiqClient
from src.liqlevels.database import MagnetDatabase
from src.liqlevels.models import LiqSnapshot, RESOLVE_MOVE_PCT
from src.liqlevels.monitor import MagnetMonitor
from src.orderflow.aggregator import OrderFlowAggregator
from src.orderflow.clients.binance import BinanceTradeClient

COINS = ["BTC", "ETH", "SOL"]

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Rejection logger — writes every eval cycle to data/rejections.log
Path("data").mkdir(parents=True, exist_ok=True)
_rej_fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_rej_fh = logging.handlers.RotatingFileHandler("data/rejections.log", maxBytes=5_000_000, backupCount=3)
_rej_fh.setFormatter(_rej_fmt)

_rej_logger = logging.getLogger("liqhunt.rejections")
_rej_logger.setLevel(logging.DEBUG)
_rej_logger.addHandler(_rej_fh)

# Shadow trade logger — signal_engine.py WARNING+ goes to same file
_shadow_logger = logging.getLogger("src.liqhunt.signal_engine")
_shadow_logger.setLevel(logging.WARNING)
_shadow_logger.addHandler(_rej_fh)

# Paper trader logger
_paper_logger = logging.getLogger("src.liqhunt.paper_trader")
_paper_logger.setLevel(logging.WARNING)
_paper_logger.addHandler(_rej_fh)

# Battle trader logger
_battle_logger = logging.getLogger("src.liqhunt.battle_trader")
_battle_logger.setLevel(logging.WARNING)
_battle_logger.addHandler(_rej_fh)


async def run_liqhunt():
    """Main async loop."""
    dashboard = LiqHuntDashboard()
    client = BinanceLiqClient()
    magnet_db = MagnetDatabase()
    monitor = MagnetMonitor(magnet_db)
    paper_trader = PaperTrader(magnet_db)
    battle_trader = BattleTrader(magnet_db)
    # Restore monitor snapshot from battle_trader position so resolution levels survive restarts
    if battle_trader.position and battle_trader.position.snapshot_price > 0:
        if battle_trader.position.direction == "SHORT":
            _long_usd, _short_usd = 2.0, 1.0
        else:
            _long_usd, _short_usd = 1.0, 2.0
        monitor.snapshot = LiqSnapshot(
            btc_price=battle_trader.position.snapshot_price,
            total_long_usd=_long_usd,
            total_short_usd=_short_usd,
            timestamp=battle_trader.position.entry_time,
        )
        logging.warning(
            "Restored snapshot @ %.0f | resolve DOWN %.0f | resolve UP %.0f",
            battle_trader.position.snapshot_price,
            battle_trader.position.snapshot_price * (1 - RESOLVE_MOVE_PCT / 100),
            battle_trader.position.snapshot_price * (1 + RESOLVE_MOVE_PCT / 100),
        )
    candle_fetcher = CandleFetcher()
    signal_engine = SignalEngine()
    last_announced_ts: float = 0
    oi_history: deque[float] = deque(maxlen=60)  # ~10 min at 10s intervals
    price_history: deque[tuple[float, float]] = deque()  # (timestamp, price) for 6h range
    cvd_history: deque[float] = deque(maxlen=30)  # 30 x ~10s snapshots ≈ 5 min of CVD

    # Seed price history from Binance 5m klines (avoids 6h cold start on VOL gate)
    await client._ensure_session()
    try:
        async with client.session.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": "BTCUSDT", "interval": "5m", "limit": 72},  # 6h
        ) as resp:
            if resp.status == 200:
                klines = await resp.json()
                for k in klines:
                    ts = k[0] / 1000  # ms → s
                    price_history.append((ts, float(k[2])))  # high
                    price_history.append((ts, float(k[3])))  # low
    except Exception:
        pass  # non-critical, will warm up naturally

    # Seed OI history from Binance 5m historical OI (avoids cold start on OI gate)
    try:
        async with client.session.get(
            "https://fapi.binance.com/futures/data/openInterestHist",
            params={"symbol": "BTCUSDT", "period": "5m", "limit": 3},
        ) as resp:
            if resp.status == 200:
                oi_data = await resp.json()
                if oi_data:
                    for point in oi_data:
                        oi_val = float(point.get("sumOpenInterestValue", 0))
                        if oi_val > 0:
                            for _ in range(30):
                                oi_history.append(oi_val)
                    logging.warning(
                        "Seeded OI history: %d entries, peak=%.0f, latest=%.0f",
                        len(oi_history), max(oi_history) if oi_history else 0,
                        oi_history[-1] if oi_history else 0,
                    )
    except Exception:
        pass  # non-critical, will warm up naturally

    # BTC-only orderflow aggregator (1m window)
    btc_flow = OrderFlowAggregator(window_minutes=1)

    def on_btc_trade(event):
        if event.coin == "BTC":
            btc_flow.add_event(event)

    btc_ws = BinanceTradeClient(coins=["BTC"], on_event=on_btc_trade)
    liq_feed = LiqFeed(symbol="btcusdt")
    ws_task = None
    liq_feed_task = None

    taker_ratio = 1.0  # neutral default, updated each cycle

    try:
        ws_task = asyncio.create_task(btc_ws.connect())
        liq_feed_task = asyncio.create_task(liq_feed.connect())

        with dashboard.create_live() as live:
            while True:
                # 1. Fetch liq data for all coins
                data = await client.get_all_coins(COINS)
                dashboard.update_data(data)

                btc_data = data.get("BTC")
                btc_price = btc_data.current_price if btc_data else 0.0

                # Track actual BTC price for 6h range (not battle-derived)
                if btc_price > 0:
                    now = _time()
                    price_history.append((now, btc_price))
                    cutoff = now - 21600
                    while price_history and price_history[0][0] < cutoff:
                        price_history.popleft()
                if len(price_history) >= 2:
                    ph_prices = [p for _, p in price_history]
                    price_range_6h = max(ph_prices) - min(ph_prices)
                else:
                    price_range_6h = monitor.price_range_6h  # fallback during warmup

                # 2. Update magnet monitor + paper trader
                prev_snap_ts = monitor.snapshot.timestamp if monitor.snapshot else 0
                battle = None
                if btc_data:
                    battle = monitor.update(btc_data, obv_macd_hist=candle_fetcher.obv_macd_histogram)

                # Paper trader: close on battle resolve or snapshot invalidation
                closed_trade = None

                # Breakeven + stop-loss check — highest priority
                if paper_trader.position is not None and btc_price > 0:
                    paper_trader.update_breakeven(btc_price)
                    stop_trade = paper_trader.check_stop_loss(btc_price, price_range_6h)
                    if stop_trade:
                        closed_trade = stop_trade
                        update_pipeline_html(magnet_db)

                # OI flip / velocity early exit — check BEFORE battle resolution
                if not closed_trade and paper_trader.position is not None and btc_data and btc_data.open_interest_usd > 0:
                    # Log OI sample for trade analysis
                    _oi_from_entry = 0.0
                    if paper_trader.position.oi_usd_at_entry > 0:
                        _oi_from_entry = (btc_data.open_interest_usd - paper_trader.position.oi_usd_at_entry) / paper_trader.position.oi_usd_at_entry * 100
                    magnet_db.log_oi_sample(paper_trader.position.entry_time, _time(), btc_data.open_interest_usd, _oi_from_entry, btc_price)

                    # Compute current OI change for mid-trade check
                    if len(oi_history) >= 2:
                        _peak_oi = max(oi_history)
                        if _peak_oi > 0:
                            _oi_now = (oi_history[-1] - _peak_oi) / _peak_oi * 100
                            paper_trader.update_oi(_oi_now, btc_data.open_interest_usd)
                            oi_exit_trade = paper_trader.check_oi_exit(btc_data.open_interest_usd, btc_price, price_range_6h)
                            if oi_exit_trade:
                                closed_trade = oi_exit_trade
                                update_pipeline_html(magnet_db)
                    # OI velocity exit — cascade stalling
                    if not closed_trade:
                        _vel = 0.0
                        if len(oi_history) >= 12:
                            _oi_2m = oi_history[-12]
                            if _oi_2m > 0:
                                _vel = (oi_history[-1] - _oi_2m) / _oi_2m * 100 / 2
                        vel_exit = paper_trader.check_oi_velocity_exit(_vel, btc_price, price_range_6h)
                        if vel_exit:
                            closed_trade = vel_exit
                            update_pipeline_html(magnet_db)

                # Max hold time exit
                if not closed_trade and paper_trader.position is not None and btc_price > 0:
                    hold_exit = paper_trader.check_max_hold(btc_price, price_range_6h)
                    if hold_exit:
                        closed_trade = hold_exit
                        update_pipeline_html(magnet_db)

                if not closed_trade and battle:
                    closed_trade = paper_trader.on_battle_resolved(battle, price_range_6h)
                elif not closed_trade and prev_snap_ts > 0 and (
                    monitor.snapshot is None or monitor.snapshot.timestamp != prev_snap_ts
                ):
                    closed_trade = paper_trader.on_snapshot_invalidated(btc_price, price_range_6h)
                if closed_trade:
                    update_pipeline_html(magnet_db)

                # Battle trader exit checks (mirrors paper_trader)
                if battle_trader.position is not None and btc_price > 0:
                    battle_trader.update_mfe_mae(btc_price)
                    be_exit = battle_trader.check_breakeven_stop(btc_price, price_range_6h)
                    if be_exit:
                        update_pipeline_html(magnet_db)
                if battle_trader.position is not None and btc_data and btc_data.open_interest_usd > 0:
                    if len(oi_history) >= 2:
                        _peak_oi = max(oi_history)
                        if _peak_oi > 0:
                            _oi_now = (oi_history[-1] - _peak_oi) / _peak_oi * 100
                            battle_trader.update_oi(_oi_now, btc_data.open_interest_usd)
                            oi_exit_b = battle_trader.check_oi_exit(btc_data.open_interest_usd, btc_price, price_range_6h, taker_ratio)
                            if oi_exit_b:
                                update_pipeline_html(magnet_db)
                if battle_trader.position is not None:
                    if battle:
                        battle_trader.on_battle_resolved(battle, price_range_6h)
                    elif prev_snap_ts > 0 and (
                        monitor.snapshot is None or monitor.snapshot.timestamp != prev_snap_ts
                    ):
                        battle_trader.on_snapshot_invalidated(btc_price, price_range_6h)

                dashboard.update_monitor_data(
                    snapshot=monitor.snapshot,
                    stats=monitor.stats,
                    stats_15x=magnet_db.get_stats_by_imbalance(1.5),
                    stats_20x=magnet_db.get_stats_by_imbalance(2.0),
                    recent_battles=monitor.recent_battles,
                )

                # 3. Fetch 5m candles
                await client._ensure_session()
                candles = await candle_fetcher.fetch(client.session)
                await candle_fetcher.fetch_ha_3m(client.session)

                # 4. Extract liq price levels for signal engine (with USD values)
                liq_levels_long: list[tuple[float, float]] = []
                liq_levels_short: list[tuple[float, float]] = []
                if btc_data:
                    liq_levels_long = [(l.price, l.estimated_usd) for l in btc_data.longs_at_risk]
                    liq_levels_short = [(l.price, l.estimated_usd) for l in btc_data.shorts_at_risk]

                # 5. Get orderflow delta (1m window)
                _, _, btc_delta_1m = btc_flow.totals()

                # 5g. CVD — cumulative volume delta (rolling ~5min)
                cvd_history.append(btc_delta_1m)
                cvd_30m = sum(cvd_history)  # running sum of recent 1m deltas

                # 5b. Compute OI change from rolling 10-min peak (not snapshot)
                # Snapshot-based OI resets every battle, missing active cascades.
                # Rolling peak detects drops regardless of snapshot resets.
                if btc_data and btc_data.open_interest_usd > 0:
                    oi_history.append(btc_data.open_interest_usd)
                oi_change_pct = 0.0
                if len(oi_history) >= 2:
                    peak_oi = max(oi_history)
                    if peak_oi > 0:
                        oi_change_pct = (oi_history[-1] - peak_oi) / peak_oi * 100

                # 5c. Compute OI velocity (%/min) from rolling history
                oi_velocity = 0.0
                if len(oi_history) >= 12:  # ~2 min of data at 10s intervals
                    oi_2min_ago = oi_history[-12]
                    if oi_2min_ago > 0:
                        oi_velocity = (oi_history[-1] - oi_2min_ago) / oi_2min_ago * 100 / 2

                # 5d. Get funding rate
                funding_rate = btc_data.funding_rate if btc_data else 0.0

                # 5e. Get real-time liquidation feed totals
                liq_long_usd, liq_short_usd, _liq_rate = liq_feed.totals()

                # 5f. Taker buy/sell ratio (regime filter)
                taker_ratio = await client.get_taker_ratio("BTC") or 1.0  # neutral default

                # Battle trader: enter on OI gate + HA alignment (no signal engine)
                if battle_trader.position is None:
                    oi_usd_now = btc_data.open_interest_usd if btc_data else 0.0
                    battle_trader.check_entry(monitor.snapshot, oi_change_pct, btc_price, oi_usd_now,
                                              ha_signal=candle_fetcher.ha_signal,
                                              taker_ratio=taker_ratio)

                # 6. Evaluate signal
                signal = signal_engine.evaluate(
                    snapshot=monitor.snapshot,
                    candles=candles,
                    avg_range=candle_fetcher.avg_range,
                    btc_price=btc_price,
                    liq_levels_long=liq_levels_long,
                    liq_levels_short=liq_levels_short,
                    btc_delta_1m=btc_delta_1m,
                    oi_change_pct=oi_change_pct,
                    funding_rate=funding_rate,
                    price_range_6h=price_range_6h,
                    oi_velocity=oi_velocity,
                    liq_long_usd=liq_long_usd,
                    liq_short_usd=liq_short_usd,
                    taker_ratio=taker_ratio,
                    cvd_30m=cvd_30m,
                )

                # Paper trader: open on signal
                if signal:
                    skip = paper_trader.skip_reason
                    if skip:
                        _rej_logger.info("SIGNAL %s @ %.0f — SKIPPED: %s | taker %.2f | cvd $%.1fM", signal.direction, signal.entry_price, skip, taker_ratio, cvd_30m / 1e6)
                    else:
                        snap_price = monitor.snapshot.btc_price if monitor.snapshot else btc_price
                        oi_usd_now = btc_data.open_interest_usd if btc_data else 0.0
                        paper_trader.on_signal(signal, snapshot_price=snap_price, oi_change_pct=oi_change_pct, oi_usd=oi_usd_now, taker_ratio=taker_ratio, cvd_30m=cvd_30m)
                        _rej_logger.info("SIGNAL %s @ %.0f | taker %.2f | cvd $%.1fM", signal.direction, signal.entry_price, taker_ratio, cvd_30m / 1e6)
                else:
                    _rej_logger.debug("REJECT %s | taker %.2f | cvd $%.1fM", signal_engine.rejection_reason, taker_ratio, cvd_30m / 1e6)

                # Compute magnet price for sweep monitor display (nearest liq on bigger side)
                magnet_price = None
                if monitor.snapshot and btc_data:
                    if monitor.snapshot.bigger_side == "LONG" and liq_levels_long:
                        magnet_price = max(p for p, _ in liq_levels_long)
                    elif liq_levels_short:
                        magnet_price = min(p for p, _ in liq_levels_short)

                # Announce new signal via TTS
                if signal and signal.timestamp != last_announced_ts:
                    last_announced_ts = signal.timestamp
                    msg = f"Signal is active based on tracked liquidations. {signal.direction} at {int(signal.entry_price)}"
                    try:
                        subprocess.Popen(["say", msg], stderr=subprocess.DEVNULL)
                    except (FileNotFoundError, OSError):
                        pass

                dashboard.update_signal_data(
                    signal=signal,
                    engine=signal_engine,
                    candle_fetcher=candle_fetcher,
                    magnet_price=magnet_price,
                    btc_delta_1m=btc_delta_1m,
                    price_range_6h=price_range_6h,
                    paper_trader=paper_trader,
                    battle_trader=battle_trader,
                )

                live.update(dashboard.render())

                # Fast polling when any position is open (2.5s), normal 10s otherwise
                if paper_trader.position is not None or battle_trader.position is not None:
                    for _ in range(4):  # 4 × 2.5s = 10s total
                        await asyncio.sleep(2.5)
                        if paper_trader.position is None and battle_trader.position is None:
                            break
                        try:
                            _price = await client.get_price("BTC") or btc_price
                            # Paper trader: breakeven + stop-loss check every poll
                            if paper_trader.position is not None:
                                paper_trader.update_breakeven(_price)
                                stop_exit = paper_trader.check_stop_loss(_price, price_range_6h)
                                if stop_exit:
                                    update_pipeline_html(magnet_db)
                            # Battle trader: MFE/MAE tracking + breakeven stop every poll
                            if battle_trader.position is not None:
                                battle_trader.update_mfe_mae(_price)
                                be_exit_b = battle_trader.check_breakeven_stop(_price, price_range_6h)
                                if be_exit_b:
                                    update_pipeline_html(magnet_db)
                            # OI flip + velocity check
                            oi_usd = await client.get_open_interest("BTC")
                            if oi_usd and oi_usd > 0:
                                oi_history.append(oi_usd)
                                # Log OI sample for paper trader analysis
                                if paper_trader.position and paper_trader.position.oi_usd_at_entry > 0:
                                    _oi_fe = (oi_usd - paper_trader.position.oi_usd_at_entry) / paper_trader.position.oi_usd_at_entry * 100
                                    magnet_db.log_oi_sample(paper_trader.position.entry_time, _time(), oi_usd, _oi_fe, _price)
                                if len(oi_history) >= 2:
                                    _peak_oi = max(oi_history)
                                    if _peak_oi > 0:
                                        _oi_now = (oi_history[-1] - _peak_oi) / _peak_oi * 100
                                        # Paper trader OI exit
                                        if paper_trader.position is not None:
                                            paper_trader.update_oi(_oi_now, oi_usd)
                                            oi_exit = paper_trader.check_oi_exit(oi_usd, _price, price_range_6h)
                                            if oi_exit:
                                                update_pipeline_html(magnet_db)
                                        # Battle trader OI exit (emergency only)
                                        if battle_trader.position is not None:
                                            battle_trader.update_oi(_oi_now, oi_usd)
                                            oi_exit_b = battle_trader.check_oi_exit(oi_usd, _price, price_range_6h, taker_ratio)
                                            if oi_exit_b:
                                                update_pipeline_html(magnet_db)
                                # OI velocity exit (paper trader only)
                                _vel = 0.0
                                if len(oi_history) >= 12:
                                    _oi_2m = oi_history[-12]
                                    if _oi_2m > 0:
                                        _vel = (oi_history[-1] - _oi_2m) / _oi_2m * 100 / 2
                                if paper_trader.position is not None:
                                    vel_exit = paper_trader.check_oi_velocity_exit(_vel, _price, price_range_6h)
                                    if vel_exit:
                                        update_pipeline_html(magnet_db)
                        except Exception:
                            pass
                else:
                    await asyncio.sleep(10)

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if liq_feed_task:
            liq_feed_task.cancel()
            try:
                await liq_feed_task
            except asyncio.CancelledError:
                pass
        if ws_task:
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                pass
        await client.close()
        magnet_db.close()


def main():
    """Sync entry point."""
    try:
        asyncio.run(run_liqhunt())
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
