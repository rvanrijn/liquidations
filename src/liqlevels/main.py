# src/liqlevels/main.py
"""Main entry point for liquidation levels dashboard."""

import asyncio
import logging

from src.liqlevels.client import BinanceLiqClient
from src.liqlevels.dashboard import LiqLevelsDashboard

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

    try:
        with dashboard.create_live() as live:
            while True:
                # Fetch data for all coins
                data = await client.get_all_coins(COINS)
                dashboard.update_data(data)
                live.update(dashboard.render())

                # Refresh every 10 seconds
                await asyncio.sleep(10)

    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await client.close()


def main():
    """Sync entry point."""
    try:
        asyncio.run(run_liqlevels())
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
