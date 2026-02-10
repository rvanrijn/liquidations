# src/liqhunt/main.py
"""Main entry point for the liquidation hunt signal engine."""

import asyncio
import logging
import subprocess

from src.liqhunt.candles import CandleFetcher
from src.liqhunt.dashboard import LiqHuntDashboard
from src.liqhunt.signal_engine import SignalEngine
from src.liqlevels.client import BinanceLiqClient
from src.liqlevels.database import MagnetDatabase
from src.liqlevels.monitor import MagnetMonitor
from src.orderflow.aggregator import OrderFlowAggregator
from src.orderflow.clients.binance import BinanceTradeClient

COINS = ["BTC", "ETH", "SOL"]

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def run_liqhunt():
    """Main async loop."""
    dashboard = LiqHuntDashboard()
    client = BinanceLiqClient()
    magnet_db = MagnetDatabase()
    monitor = MagnetMonitor(magnet_db)
    candle_fetcher = CandleFetcher()
    signal_engine = SignalEngine()
    last_announced_ts: float = 0

    # BTC-only orderflow aggregator (1m window)
    btc_flow = OrderFlowAggregator(window_minutes=1)

    def on_btc_trade(event):
        if event.coin == "BTC":
            btc_flow.add_event(event)

    btc_ws = BinanceTradeClient(coins=["BTC"], on_event=on_btc_trade)
    ws_task = None

    try:
        ws_task = asyncio.create_task(btc_ws.connect())

        with dashboard.create_live() as live:
            while True:
                # 1. Fetch liq data for all coins
                data = await client.get_all_coins(COINS)
                dashboard.update_data(data)

                btc_data = data.get("BTC")
                btc_price = btc_data.current_price if btc_data else 0.0

                # 2. Update magnet monitor
                if btc_data:
                    monitor.update(btc_data)

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

                # 4. Extract liq price levels for signal engine
                liq_levels_long: list[float] = []
                liq_levels_short: list[float] = []
                if btc_data:
                    liq_levels_long = [l.price for l in btc_data.longs_at_risk]
                    liq_levels_short = [l.price for l in btc_data.shorts_at_risk]

                # 5. Get orderflow delta (1m window)
                _, _, btc_delta_1m = btc_flow.totals()

                # 5b. Compute OI change from snapshot
                oi_change_pct = 0.0
                if monitor.snapshot and monitor.snapshot.open_interest_usd > 0 and btc_data:
                    current_oi = btc_data.open_interest_usd
                    if current_oi > 0:
                        oi_change_pct = (current_oi - monitor.snapshot.open_interest_usd) / monitor.snapshot.open_interest_usd * 100

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
                )

                # Compute magnet price for sweep monitor display (nearest liq on bigger side)
                magnet_price = None
                if monitor.snapshot and btc_data:
                    if monitor.snapshot.bigger_side == "LONG" and liq_levels_long:
                        magnet_price = max(liq_levels_long)
                    elif liq_levels_short:
                        magnet_price = min(liq_levels_short)

                # Announce new signal via TTS
                if signal and signal.timestamp != last_announced_ts:
                    last_announced_ts = signal.timestamp
                    msg = f"Signal is active based on tracked liquidations. {signal.direction} at {int(signal.entry_price)}"
                    subprocess.Popen(["say", msg])

                dashboard.update_signal_data(
                    signal=signal,
                    engine=signal_engine,
                    candle_fetcher=candle_fetcher,
                    magnet_price=magnet_price,
                    btc_delta_1m=btc_delta_1m,
                )

                live.update(dashboard.render())
                await asyncio.sleep(10)

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
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
