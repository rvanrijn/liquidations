# src/main.py
import asyncio
import logging
from src.aggregator import LiquidationAggregator
from src.clients.bybit import BybitClient
from src.clients.binance import BinanceClient
from src.ui.dashboard import Dashboard

# Top coins by volume
COINS = [
    "BTC", "ETH", "SOL", "XRP", "DOGE",
    "ADA", "AVAX", "LINK", "DOT", "MATIC",
    "UNI", "LTC", "BCH", "ATOM", "APT",
]

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def run_dashboard():
    """Main async entry point."""
    aggregator = LiquidationAggregator()
    dashboard = Dashboard(aggregator)

    def on_event(event):
        aggregator.add_event(event)

    # Create clients
    bybit = BybitClient(coins=COINS, on_event=on_event)
    binance = BinanceClient(coins=COINS, on_event=on_event)

    # Track connection status
    original_bybit_connect = bybit.connect
    original_binance_connect = binance.connect

    async def bybit_connect_wrapper():
        try:
            dashboard.set_connection_status("bybit", True)
            await original_bybit_connect()
        except Exception:
            dashboard.set_connection_status("bybit", False)
            raise

    async def binance_connect_wrapper():
        try:
            dashboard.set_connection_status("binance", True)
            await original_binance_connect()
        except Exception:
            dashboard.set_connection_status("binance", False)
            raise

    bybit.connect = bybit_connect_wrapper
    binance.connect = binance_connect_wrapper

    # Start WebSocket tasks
    ws_tasks = [
        asyncio.create_task(bybit.connect()),
        asyncio.create_task(binance.connect()),
    ]

    # Run dashboard
    try:
        with dashboard.create_live() as live:
            while True:
                live.update(dashboard.render())
                await asyncio.sleep(0.5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for task in ws_tasks:
            task.cancel()


def main():
    """Sync entry point."""
    try:
        asyncio.run(run_dashboard())
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
