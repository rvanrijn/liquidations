# src/liqlevels/main.py
"""Main entry point for liquidation levels dashboard."""

import asyncio
import logging

from src.liqlevels.client import BinanceLiqClient
from src.liqlevels.dashboard import LiqLevelsDashboard
from src.liqlevels.database import MagnetDatabase
from src.liqlevels.monitor import MagnetMonitor

# Top coins to track
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "MATIC"]

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def run_liqlevels():
    """Main async entry point."""
    dashboard = LiqLevelsDashboard()
    client = BinanceLiqClient()
    magnet_db = MagnetDatabase()
    monitor = MagnetMonitor(magnet_db)

    try:
        with dashboard.create_live() as live:
            while True:
                # Fetch data for all coins
                data = await client.get_all_coins(COINS)
                dashboard.update_data(data)

                # Feed BTC data to magnet monitor
                btc_data = data.get("BTC")
                if btc_data:
                    monitor.update(btc_data)

                # Pass monitor state to dashboard
                dashboard.update_monitor_data(
                    snapshot=monitor.snapshot,
                    stats=monitor.stats,
                    stats_15x=magnet_db.get_stats_by_imbalance(1.5),
                    stats_20x=magnet_db.get_stats_by_imbalance(2.0),
                    recent_battles=monitor.recent_battles,
                )

                live.update(dashboard.render())

                # Refresh every 10 seconds
                await asyncio.sleep(10)

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await client.close()
        magnet_db.close()


def main():
    """Sync entry point."""
    try:
        asyncio.run(run_liqlevels())
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
