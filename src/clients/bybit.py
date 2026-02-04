# src/clients/bybit.py
from src.clients.base import BaseExchangeClient
from src.models import LiquidationEvent


class BybitClient(BaseExchangeClient):
    exchange_name = "bybit"
    ws_url = "wss://stream.bybit.com/v5/public/linear"

    def get_subscribe_message(self) -> dict:
        args = [f"allLiquidation.{coin}USDT" for coin in self.coins]
        return {"op": "subscribe", "args": args}

    def parse_message(self, data: dict) -> LiquidationEvent | None:
        # Check if this is a liquidation message
        topic = data.get("topic", "")
        if not topic.startswith("allLiquidation."):
            return None

        liq_data = data.get("data")
        if not liq_data:
            return None

        # Extract coin from symbol (e.g., "BTCUSDT" -> "BTC")
        symbol = liq_data.get("s", "")
        coin = symbol.replace("USDT", "").replace("PERP", "")

        # "Buy" means a long was liquidated, "Sell" means a short was liquidated
        side = "long" if liq_data.get("S") == "Buy" else "short"

        size = float(liq_data.get("v", 0))
        price = float(liq_data.get("p", 0))
        value_usd = size * price

        return LiquidationEvent(
            exchange=self.exchange_name,
            coin=coin,
            side=side,
            size=size,
            price=price,
            value_usd=value_usd,
            timestamp=liq_data.get("T", data.get("ts", 0)),
            wallet=None,  # Bybit doesn't provide wallet
        )
