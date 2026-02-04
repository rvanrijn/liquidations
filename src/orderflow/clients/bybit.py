# src/orderflow/clients/bybit.py
from src.orderflow.clients.base import BaseTradeClient
from src.orderflow.models import TradeEvent


class BybitTradeClient(BaseTradeClient):
    exchange_name = "bybit"
    ws_url = "wss://stream.bybit.com/v5/public/linear"

    # Size thresholds in USD
    WHALE_THRESHOLD = 1_000_000
    LARGE_THRESHOLD = 100_000
    MEDIUM_THRESHOLD = 10_000
    SMALL_THRESHOLD = 1_000

    def get_subscribe_message(self) -> dict:
        args = [f"publicTrade.{coin}USDT" for coin in self.coins]
        return {"op": "subscribe", "args": args}

    def _classify_size(self, value_usd: float) -> str:
        """Classify trade size based on USD value."""
        if value_usd >= self.WHALE_THRESHOLD:
            return "whale"
        elif value_usd >= self.LARGE_THRESHOLD:
            return "large"
        elif value_usd >= self.MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "small"

    def parse_message(self, data: dict) -> TradeEvent | None:
        # Check if this is a trade message
        topic = data.get("topic", "")
        if not topic.startswith("publicTrade."):
            return None

        trade_list = data.get("data")
        if not trade_list or not isinstance(trade_list, list):
            return None

        # Process only the first trade in the batch
        # (in practice, we could iterate and emit multiple events)
        trade_data = trade_list[0]

        # Extract coin from symbol (e.g., "BTCUSDT" -> "BTC")
        symbol = trade_data.get("s", "")
        coin = symbol.replace("USDT", "").replace("PERP", "")

        # "Buy" = buyer is taker, "Sell" = seller is taker
        side = "buy" if trade_data.get("S") == "Buy" else "sell"

        size = float(trade_data.get("v", 0))
        price = float(trade_data.get("p", 0))
        value_usd = size * price

        # Filter out trades below $1,000
        if value_usd < self.SMALL_THRESHOLD:
            return None

        return TradeEvent(
            exchange=self.exchange_name,
            coin=coin,
            side=side,
            size=size,
            price=price,
            value_usd=value_usd,
            timestamp=trade_data.get("T", data.get("ts", 0)),
            size_category=self._classify_size(value_usd),
        )
