# src/krakenbot/main.py
"""Main entry point for the Kraken live trading bot."""

import asyncio
import json
import logging
import logging.handlers
from collections import deque
from pathlib import Path
from time import time as _time

import aiohttp

from src.krakenbot.config import BotConfig
from src.krakenbot.dashboard import KrakenBotDashboard
from src.krakenbot.database import KrakenBotDatabase
from src.krakenbot.executor import KrakenExecutor
from src.krakenbot.liq_client import BinanceLiqClient
from src.krakenbot.liq_models import LiqSnapshot, RESOLVE_MOVE_PCT
from src.krakenbot.models import LivePosition, LiveTrade
from src.krakenbot.monitor import MagnetMonitor
from src.krakenbot.risk_manager import RiskManager

MIN_HOLD_SEC = 4 * 60  # 4 min — don't allow early exits before this
MAX_HOLD_SEC = 60 * 60  # 60 min — close trade if still open after this
OI_EMERGENCY_PCT = 0.5  # emergency exit: bypass hold time if OI spikes > +0.5% from entry
OI_EMERGENCY_MIN_SEC = 180  # minimum 3 min before emergency exit can fire
OI_GATE_PCT = -0.1      # OI must drop at least this much (backtest optimal)
MIN_IMBALANCE = 1.25    # minimum imbalance ratio

COINS = ["BTC", "ETH", "SOL"]

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def _setup_logging(config: BotConfig):
    """Set up file logging for the bot."""
    Path(config.log_dir).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # Rejection logger
    rej_fh = logging.handlers.RotatingFileHandler(
        f"{config.log_dir}/rejections.log", maxBytes=5_000_000, backupCount=3,
    )
    rej_fh.setFormatter(fmt)
    rej_logger = logging.getLogger("krakenbot.rejections")
    rej_logger.setLevel(logging.DEBUG)
    rej_logger.addHandler(rej_fh)

    # Executor logger
    exec_logger = logging.getLogger("src.krakenbot.executor")
    exec_logger.setLevel(logging.INFO)
    exec_fh = logging.handlers.RotatingFileHandler(
        f"{config.log_dir}/executor.log", maxBytes=5_000_000, backupCount=3,
    )
    exec_fh.setFormatter(fmt)
    exec_logger.addHandler(exec_fh)

    return rej_logger


async def _run_price_ws(ws_state: dict):
    """Background websocket: instant BTC price from Binance futures."""
    url = "wss://fstream.binance.com/ws/btcusdt@aggTrade"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=30) as ws:
                    logging.warning("Price websocket connected")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            price = float(data["p"])
                            ws_state["price"] = price
                            hi = ws_state.get("resolve_up", 0)
                            lo = ws_state.get("resolve_down", 0)
                            if hi > 0 and lo > 0 and (price >= hi or price <= lo):
                                ws_state["resolve_event"].set()
        except Exception as e:
            logging.warning("Price websocket error: %s — reconnecting", e)
            await asyncio.sleep(2)


async def run_bot():
    """Main async loop."""
    config = BotConfig()
    _rej_logger = _setup_logging(config)

    db = KrakenBotDatabase(config.db_path)
    executor = KrakenExecutor(config, db)
    risk_mgr = RiskManager(config, db)
    dashboard = KrakenBotDashboard(config)
    client = BinanceLiqClient()
    monitor = MagnetMonitor(db)

    # Restore persisted position
    position: LivePosition | None = db.load_live_position()
    balance = db.get_live_balance() or 118.0  # Kraken futures starting balance (USDC)
    trade_stats = db.get_live_stats()
    recent_trades = db.get_recent_live_trades()
    pending_limit: dict | None = None  # tracks pending limit order

    if position:
        logging.warning(
            "Restored position: %s @ %.0f | size %.0f | snap %.0f",
            position.direction, position.entry_price, position.size_contracts,
            position.snapshot_price,
        )
        # Restore monitor snapshot so battle resolution levels survive restarts
        if position.snapshot_price > 0:
            # Reconstruct bigger_side from trade direction (SHORT trade = LONG magnet, LONG trade = SHORT magnet)
            if position.direction == "SHORT":
                _long_usd, _short_usd = 2.0, 1.0  # LONG bigger
            else:
                _long_usd, _short_usd = 1.0, 2.0  # SHORT bigger
            monitor.snapshot = LiqSnapshot(
                btc_price=position.snapshot_price,
                total_long_usd=_long_usd,
                total_short_usd=_short_usd,
                timestamp=position.entry_time,
                open_interest_usd=position.oi_usd_at_entry,
            )
            logging.warning(
                "Restored snapshot @ %.0f | resolve DOWN %.0f | resolve UP %.0f",
                position.snapshot_price,
                position.snapshot_price * (1 - RESOLVE_MOVE_PCT / 100),
                position.snapshot_price * (1 + RESOLVE_MOVE_PCT / 100),
            )

    oi_history: deque[float] = deque(maxlen=60)
    price_history: deque[tuple[float, float]] = deque()

    # Websocket state for instant price resolution
    ws_state: dict = {
        "price": 0.0,
        "resolve_up": 0.0,
        "resolve_down": 0.0,
        "resolve_event": asyncio.Event(),
    }
    ws_task = asyncio.create_task(_run_price_ws(ws_state))

    # Initialize executor
    if not await executor.initialize():
        if not config.dry_run:
            logging.error("Executor failed to initialize — exiting")
            return
        logging.warning("Executor init failed but dry-run mode — continuing")

    # Reconcile position with Kraken on startup
    await executor.reconcile_position()

    # Update balance from Kraken if not dry-run
    if not config.dry_run:
        kraken_balance = await executor.get_balance()
        if kraken_balance is not None:
            balance = kraken_balance

    # Seed price history from Binance 5m klines
    await client._ensure_session()
    try:
        async with client.session.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": "BTCUSDT", "interval": "5m", "limit": 72},
        ) as resp:
            if resp.status == 200:
                klines = await resp.json()
                for k in klines:
                    ts = k[0] / 1000
                    price_history.append((ts, float(k[2])))
                    price_history.append((ts, float(k[3])))
    except Exception:
        pass

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

    try:

        with dashboard.create_live() as live:
            while True:
                # 1. Fetch liq data
                data = await client.get_all_coins(COINS)
                dashboard.update_data(data)

                btc_data = data.get("BTC")
                btc_price = btc_data.current_price if btc_data else 0.0
                # Use websocket price if fresher
                if ws_state["price"] > 0:
                    btc_price = ws_state["price"]

                # Track BTC price for 6h range
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
                    price_range_6h = monitor.price_range_6h

                # 2. Update magnet monitor
                prev_snap_ts = monitor.snapshot.timestamp if monitor.snapshot else 0
                battle = None
                if btc_data:
                    battle = monitor.update(btc_data)

                # Cancel pending limit if battle resolved (snapshot gone or changed)
                if pending_limit and battle:
                    await executor.cancel_order(pending_limit["order_id"])
                    _rej_logger.info("LIMIT CANCELLED: battle resolved")
                    pending_limit = None

                # Breakeven stop — move stop to entry once +0.20% profit
                closed_trade = None
                if position and btc_price > 0:
                    if position.direction == "LONG":
                        _unrealized = (btc_price - position.entry_price) / position.entry_price * 100
                    else:
                        _unrealized = (position.entry_price - btc_price) / position.entry_price * 100
                    if _unrealized > position.mfe_pct:
                        position.mfe_pct = _unrealized
                        db.save_live_position(position)
                    if _unrealized < position.mae_pct:
                        position.mae_pct = _unrealized
                        db.save_live_position(position)

                # OI flip early exit — check BEFORE battle resolution
                _held = (_time() - position.entry_time) if position else 0
                if not closed_trade and position and position.oi_usd_at_entry > 0 and btc_data and btc_data.open_interest_usd > 0:
                    oi_change_from_entry = (btc_data.open_interest_usd - position.oi_usd_at_entry) / position.oi_usd_at_entry * 100
                    # Log OI sample for trade analysis
                    db.log_oi_sample(position.entry_time, _time(), btc_data.open_interest_usd, oi_change_from_entry, btc_price)
                    # Emergency exit: bypass hold time if OI spikes hard (> +0.5%) AND CVD confirms
                    if _held >= OI_EMERGENCY_MIN_SEC and _held < MIN_HOLD_SEC and oi_change_from_entry >= OI_EMERGENCY_PCT:
                        taker = await client.get_taker_ratio("BTC")
                        # CVD confirms reversal: rising (>1) bad for SHORT, falling (<1) bad for LONG
                        cvd_confirms = taker is None or (
                            (position.direction == "SHORT" and taker > 1.0)
                            or (position.direction == "LONG" and taker < 1.0)
                        )
                        if cvd_confirms:
                            logging.warning(
                                "EMERGENCY OI EXIT: OI +%.2f%% from entry (>%.1f%%) after only %.0fs | taker %.2f | closing %s @ %.0f",
                                oi_change_from_entry, OI_EMERGENCY_PCT, _held,
                                taker or 0, position.direction, btc_price,
                            )
                            try:
                                closed_trade = await _close_position(
                                    executor, db, position, btc_price,
                                    balance, price_range_6h, "oi_emergency",
                                )
                            except Exception as e:
                                logging.error("Critical: emergency OI close failed: %s", e)
                        else:
                            logging.warning(
                                "EMERGENCY OI BLOCKED by CVD: OI +%.2f%% but taker %.2f favors %s — holding",
                                oi_change_from_entry, taker or 0, position.direction,
                            )

                # Close position on battle resolve or snapshot invalidation
                if not closed_trade and battle and position:
                    try:
                        closed_trade = await _close_position(
                            executor, db, position, battle.resolved_price,
                            balance, price_range_6h, "battle_resolved",
                        )
                    except Exception as e:
                        logging.error("Critical: battle close failed: %s", e)
                elif not closed_trade and position and prev_snap_ts > 0 and (
                    monitor.snapshot is None or monitor.snapshot.timestamp != prev_snap_ts
                ):
                    try:
                        closed_trade = await _close_position(
                            executor, db, position, btc_price,
                            balance, price_range_6h, "snapshot_invalidated",
                        )
                    except Exception as e:
                        logging.error("Critical: snapshot close failed: %s", e)

                if closed_trade:
                    balance = closed_trade.balance_after
                    risk_mgr.record_trade(closed_trade.pnl_usd)
                    position = None
                    trade_stats = db.get_live_stats()
                    recent_trades = db.get_recent_live_trades()

                dashboard.update_monitor_data(
                    snapshot=monitor.snapshot,
                    stats=monitor.stats,
                    recent_battles=monitor.recent_battles,
                )

                # 3. OI change (needed for entry gate + exit logic)
                if btc_data and btc_data.open_interest_usd > 0:
                    oi_history.append(btc_data.open_interest_usd)
                oi_change_pct = 0.0
                if len(oi_history) >= 2:
                    peak_oi = max(oi_history)
                    if peak_oi > 0:
                        oi_change_pct = (oi_history[-1] - peak_oi) / peak_oi * 100

                # Log market data for backtesting (every cycle ~10s)
                if btc_price > 0 and btc_data:
                    taker = await client.get_taker_ratio("BTC")
                    imb = monitor.snapshot.imbalance_ratio if monitor.snapshot else 0.0
                    side = monitor.snapshot.bigger_side if monitor.snapshot else ""
                    db.log_market_sample(
                        _time(), btc_price, btc_data.open_interest_usd,
                        taker or 0.0, imb, side, oi_change_pct,
                    )

                # Heartbeat: log state every ~3 min (12 cycles × ~15s)
                if not hasattr(run_bot, '_hb_count'):
                    run_bot._hb_count = 0
                run_bot._hb_count += 1
                if run_bot._hb_count % 12 == 0:
                    snap_info = "no snapshot"
                    if monitor.snapshot:
                        s = monitor.snapshot
                        win = s.btc_price * (1 - RESOLVE_MOVE_PCT / 100)
                        loss = s.btc_price * (1 + RESOLVE_MOVE_PCT / 100)
                        if s.bigger_side == "SHORT":
                            win, loss = loss, win
                        dist_to_win = abs(btc_price - win) / btc_price * 100
                        snap_info = "snap %.0f | imb %.2fx | %s | win %.0f (%.2f%%) | loss %.0f" % (
                            s.btc_price, s.imbalance_ratio, s.bigger_side, win, dist_to_win, loss)
                    pos_info = position.direction if position else ("limit@%.0f" % pending_limit["limit_price"] if pending_limit else "none")
                    _rej_logger.info(
                        "HEARTBEAT | BTC %.0f | OI %+.2f%% | %s | pos=%s",
                        btc_price, oi_change_pct, snap_info, pos_info,
                    )

                # 4. Battle trader entry: limit order at snapshot price
                # Place limit when OI gate opens, cancel if snapshot changes
                if position is None and monitor.snapshot is not None:
                    direction = "SHORT" if monitor.snapshot.bigger_side == "LONG" else "LONG"
                    snap_price = monitor.snapshot.btc_price

                    # Check if pending limit order's snapshot changed → cancel stale order
                    if pending_limit and pending_limit["snapshot_price"] != snap_price:
                        await executor.cancel_order(pending_limit["order_id"])
                        _rej_logger.info("LIMIT CANCELLED: snapshot changed %.0f → %.0f",
                                         pending_limit["snapshot_price"], snap_price)
                        pending_limit = None

                    # Check if pending limit order got filled
                    if pending_limit:
                        if config.dry_run:
                            # Dry-run: simulate fill when price crosses the limit price
                            lp = pending_limit["limit_price"]
                            filled = (direction == "SHORT" and btc_price >= lp) or \
                                     (direction == "LONG" and btc_price <= lp)
                            if filled:
                                fill_price, fill_size = lp, pending_limit["size"]
                            else:
                                fill_price, fill_size = 0.0, 0.0
                        else:
                            filled, fill_price, fill_size = await executor.check_order_filled(
                                pending_limit["order_id"])
                        if filled:
                            oi_usd_now = btc_data.open_interest_usd if btc_data else 0.0
                            position = LivePosition(
                                direction=pending_limit["direction"],
                                entry_price=fill_price or pending_limit["limit_price"],
                                entry_time=_time(),
                                size_contracts=fill_size or pending_limit["size"],
                                notional_usd=balance * config.leverage,
                                stop_price=0.0,
                                snapshot_price=pending_limit["snapshot_price"],
                                kraken_order_id=pending_limit["order_id"],
                                cli_ord_id=pending_limit["cli_ord_id"],
                                oi_usd_at_entry=oi_usd_now,
                            )
                            db.save_live_position(position)
                            _rej_logger.info(
                                "LIMIT FILLED %s @ %.0f | snap %.0f | imb %.1fx | size=%.4f | order=%s",
                                position.direction, position.entry_price, position.snapshot_price,
                                monitor.snapshot.imbalance_ratio, position.size_contracts,
                                pending_limit["order_id"],
                            )
                            pending_limit = None

                    # Place new limit order when OI gate opens
                    if (position is None and pending_limit is None
                        and oi_change_pct <= OI_GATE_PCT
                        and monitor.snapshot.imbalance_ratio >= MIN_IMBALANCE):

                        risk_check = risk_mgr.check_entry(
                            has_position=False, balance=balance,
                            entry_price=snap_price, stop_price=0.0,
                        )
                        if risk_check.allowed:
                            size = executor.calculate_position_size(balance, snap_price)
                            if size > 0:
                                result = await executor.place_limit_order(direction, size, snap_price)
                                if result.success:
                                    pending_limit = {
                                        "order_id": result.order_id,
                                        "cli_ord_id": result.cli_ord_id,
                                        "direction": direction,
                                        "limit_price": snap_price,
                                        "snapshot_price": snap_price,
                                        "size": size,
                                        "placed_at": _time(),
                                    }
                                    _rej_logger.info(
                                        "LIMIT PLACED %s @ %.0f (snap) | OI %.2f%% | imb %.1fx | size=%.4f | order=%s",
                                        direction, snap_price, oi_change_pct,
                                        monitor.snapshot.imbalance_ratio, size, result.order_id,
                                    )
                                    # Check if immediately filled (price already at/beyond limit)
                                    if result.filled_size > 0:
                                        oi_usd_now = btc_data.open_interest_usd if btc_data else 0.0
                                        position = LivePosition(
                                            direction=direction,
                                            entry_price=result.filled_price or snap_price,
                                            entry_time=_time(),
                                            size_contracts=result.filled_size,
                                            notional_usd=balance * config.leverage,
                                            stop_price=0.0,
                                            snapshot_price=snap_price,
                                            kraken_order_id=result.order_id,
                                            cli_ord_id=result.cli_ord_id,
                                            oi_usd_at_entry=oi_usd_now,
                                        )
                                        db.save_live_position(position)
                                        _rej_logger.info(
                                            "LIMIT IMMEDIATE FILL %s @ %.0f | order=%s",
                                            direction, position.entry_price, result.order_id,
                                        )
                                        pending_limit = None
                        else:
                            _rej_logger.info("RISK BLOCKED: %s", risk_check.reason)
                dashboard.update_signal_data(
                    oi_change_pct=oi_change_pct,
                    price_range_6h=price_range_6h,
                )
                dashboard.update_position_data(
                    position=position,
                    balance=balance,
                    stats=trade_stats,
                    recent_trades=recent_trades,
                    risk_manager=risk_mgr,
                )

                live.update(dashboard.render())

                # Update websocket resolve levels
                if position and monitor.snapshot:
                    ws_state["resolve_up"] = monitor.snapshot.btc_price * (1 + RESOLVE_MOVE_PCT / 100)
                    ws_state["resolve_down"] = monitor.snapshot.btc_price * (1 - RESOLVE_MOVE_PCT / 100)
                else:
                    ws_state["resolve_up"] = 0.0
                    ws_state["resolve_down"] = 0.0
                    ws_state["resolve_event"].clear()

                # Fast OI polling when position is open (2.5s), normal 10s otherwise
                if position is not None and position.oi_usd_at_entry > 0:
                    for _ in range(4):  # 4 × 2.5s = 10s total
                        try:
                            await asyncio.wait_for(ws_state["resolve_event"].wait(), timeout=2.5)
                            ws_state["resolve_event"].clear()
                            break  # price crossed resolve level — handle in main loop
                        except asyncio.TimeoutError:
                            pass
                        if position is None:
                            break
                        _held_poll = _time() - position.entry_time
                        try:
                            _price = ws_state["price"] or await client.get_price("BTC") or btc_price
                            # Breakeven check every poll
                            if position.direction == "LONG":
                                _unrealized = (_price - position.entry_price) / position.entry_price * 100
                            else:
                                _unrealized = (position.entry_price - _price) / position.entry_price * 100
                            if _unrealized > position.mfe_pct:
                                position.mfe_pct = _unrealized
                                db.save_live_position(position)
                            if _unrealized < position.mae_pct:
                                position.mae_pct = _unrealized
                                db.save_live_position(position)
                            oi_usd = await client.get_open_interest("BTC")
                            if oi_usd and oi_usd > 0:
                                oi_history.append(oi_usd)
                                oi_change_from_entry = (oi_usd - position.oi_usd_at_entry) / position.oi_usd_at_entry * 100
                                # Log OI sample for trade analysis
                                db.log_oi_sample(position.entry_time, _time(), oi_usd, oi_change_from_entry, btc_price)
                                # Emergency exit: bypass hold time if OI spikes hard AND CVD confirms
                                if _held_poll >= OI_EMERGENCY_MIN_SEC and _held_poll < MIN_HOLD_SEC and oi_change_from_entry >= OI_EMERGENCY_PCT:
                                    taker = await client.get_taker_ratio("BTC")
                                    cvd_confirms = taker is None or (
                                        (position.direction == "SHORT" and taker > 1.0)
                                        or (position.direction == "LONG" and taker < 1.0)
                                    )
                                    if cvd_confirms:
                                        logging.warning(
                                            "EMERGENCY OI EXIT (poll): OI +%.2f%% from entry (>%.1f%%) after only %.0fs | taker %.2f | closing %s @ %.0f",
                                            oi_change_from_entry, OI_EMERGENCY_PCT, _held_poll,
                                            taker or 0, position.direction, _price,
                                        )
                                        try:
                                            closed_trade = await _close_position(
                                                executor, db, position, _price,
                                                balance, price_range_6h, "oi_emergency",
                                            )
                                        except Exception as e:
                                            logging.error("Critical: emergency OI poll close failed: %s", e)
                                            closed_trade = None
                                    else:
                                        logging.warning(
                                            "EMERGENCY OI BLOCKED (poll): OI +%.2f%% but taker %.2f favors %s — holding",
                                            oi_change_from_entry, taker or 0, position.direction,
                                        )
                                        closed_trade = None
                                    if closed_trade:
                                        balance = closed_trade.balance_after
                                        risk_mgr.record_trade(closed_trade.pnl_usd)
                                        position = None
                                        trade_stats = db.get_live_stats()
                                        recent_trades = db.get_recent_live_trades()
                                        break
                        except Exception:
                            pass
                else:
                    try:
                        await asyncio.wait_for(ws_state["resolve_event"].wait(), timeout=10)
                        ws_state["resolve_event"].clear()
                    except asyncio.TimeoutError:
                        pass

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        ws_task.cancel()
        await client.close()
        db.close()


async def _close_position(
    executor: KrakenExecutor,
    db: KrakenBotDatabase,
    position: LivePosition,
    exit_price: float,
    balance: float,
    price_range_6h: float,
    reason: str,
) -> LiveTrade | None:
    """Close a live position and log the trade."""
    result = await executor.close_position(
        position.direction, position.size_contracts, exit_price, reason,
    )
    if not result.success:
        logging.error("Failed to close position: %s", result.error)
        # Check if Kraken still has the position — if not, clear stale DB state
        kraken_positions = await executor.get_open_positions()
        has_position = any(
            p.get("symbol") == executor.config.symbol for p in kraken_positions
        )
        if not has_position:
            logging.warning("Kraken has no position — clearing stale DB position")
            db.clear_live_position()
        return None

    if position.direction == "LONG":
        move_pct = (exit_price - position.entry_price) / position.entry_price * 100
    else:
        move_pct = (position.entry_price - exit_price) / position.entry_price * 100

    pnl = move_pct / 100 * position.notional_usd
    total_fees = result.fee_usd
    new_balance = balance + pnl - total_fees

    trade = LiveTrade(
        direction=position.direction,
        entry_price=position.entry_price,
        exit_price=exit_price,
        entry_time=position.entry_time,
        exit_time=_time(),
        size_contracts=position.size_contracts,
        notional_usd=position.notional_usd,
        pnl_usd=pnl,
        fee_usd=total_fees,
        balance_after=new_balance,
        move_pct=move_pct,
        exit_reason=reason,
        entry_order_id=position.kraken_order_id,
        exit_order_id=result.order_id,
        taker_ratio=getattr(position, 'taker_ratio', 1.0),
        cvd_30m=getattr(position, 'cvd_30m', 0.0),
        mfe_pct=getattr(position, 'mfe_pct', 0.0),
        mae_pct=getattr(position, 'mae_pct', 0.0),
    )

    db.log_live_trade(trade)
    db.clear_live_position()
    logging.warning(
        "CLOSE %s @ %.0f | PnL $%+.2f | Balance $%.2f | %s",
        trade.direction, exit_price, pnl, new_balance, reason,
    )
    return trade


def main():
    """Sync entry point."""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
