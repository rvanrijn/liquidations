# src/liqlevels/main.py
"""Main entry point for liquidation levels dashboard."""

import asyncio
import logging
from dotenv import load_dotenv

from src.liqlevels.client import CoinglassClient
from src.liqlevels.dashboard import LiqLevelsDashboard

# Load .env file
load_dotenv()

# Top coins to track
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "MATIC"]

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def run_liqlevels():
    """Main async entry point."""
    dashboard = LiqLevelsDashboard()

    try:
        client = CoinglassClient()
    except ValueError as e:
        print(f"\n❌ {e}")
        print("\nTo use this dashboard:")
        print("1. Get a free API key at https://www.coinglass.com/pricing")
        print("2. Set it: export COINGLASS_API_KEY=your_key")
        print("   Or add to .env file: COINGLASS_API_KEY=your_key\n")
        return

    try:
        with dashboard.create_live() as live:
            while True:
                # Fetch data for all coins
                data = await client.get_all_coins(COINS)
                dashboard.update_data(data)
                live.update(dashboard.render())

                # Refresh every 30 seconds (API rate limits)
                await asyncio.sleep(30)

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
