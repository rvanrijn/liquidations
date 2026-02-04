# src/clients/binance.py
from src.clients.base import BaseExchangeClient
from src.models import LiquidationEvent


class BinanceClient(BaseExchangeClient):
    exchange_name = "binance"

    def __init__(self, coins: list[str], on_event):
        super().__init__(coins, on_event)
        # Binance uses combined stream URL
        streams = "/".join(f"{coin.lower()}usdt@forceOrder" for coin in coins)
        self.ws_url = f"wss://fstream.binance.com/stream?streams={streams}"

    def get_subscribe_message(self) -> dict:
        # Binance combined stream doesn't need subscription message
        return {"method": "REQUEST", "params": [], "id": 1}

    def parse_message(self, data: dict) -> LiquidationEvent | None:
        # Check if this is a forceOrder message
        if "data" not in data or data.get("data", {}).get("e") != "forceOrder":
            return None

        order = data["data"].get("o", {})
        if not order:
            return None

        # Extract coin from symbol
        symbol = order.get("s", "")
        coin = symbol.replace("USDT", "").replace("PERP", "")

        # "SELL" means a long was liquidated, "BUY" means a short was liquidated
        side = "long" if order.get("S") == "SELL" else "short"

        size = float(order.get("z", order.get("q", 0)))  # Filled qty or original qty
        price = float(order.get("ap", order.get("p", 0)))  # Avg price or order price
        value_usd = size * price

        return LiquidationEvent(
            exchange=self.exchange_name,
            coin=coin,
            side=side,
            size=size,
            price=price,
            value_usd=value_usd,
            timestamp=order.get("T", data["data"].get("E", 0)),
            wallet=None,  # Binance doesn't provide wallet
        )
