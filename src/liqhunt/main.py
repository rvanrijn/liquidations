# src/liqhunt/main.py
"""Main entry point for the liquidation hunt signal engine."""

import asyncio
import logging

from src.liqhunt.candles import CandleFetcher
from src.liqhunt.dashboard import LiqHuntDashboard
from src.liqhunt.signal_engine import SignalEngine
from src.liqlevels.client import BinanceLiqClient
from src.liqlevels.database import MagnetDatabase
from src.liqlevels.monitor import MagnetMonitor

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

    try:
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

                # 5. Evaluate signal
                signal = signal_engine.evaluate(
                    snapshot=monitor.snapshot,
                    candles=candles,
                    avg_range=candle_fetcher.avg_range,
                    btc_price=btc_price,
                    liq_levels_long=liq_levels_long,
                    liq_levels_short=liq_levels_short,
                )

                # Compute magnet price for sweep monitor display
                magnet_price = None
                if monitor.snapshot:
                    snap = monitor.snapshot
                    magnet_price = (
                        snap.nearest_long_price
                        if snap.bigger_side == "LONG"
                        else snap.nearest_short_price
                    )

                dashboard.update_signal_data(
                    signal=signal,
                    engine=signal_engine,
                    candle_fetcher=candle_fetcher,
                    magnet_price=magnet_price,
                )

                live.update(dashboard.render())
                await asyncio.sleep(10)

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
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
