# src/orderflow/clients/binance.py
from src.orderflow.clients.base import BaseTradeClient
from src.orderflow.models import TradeEvent


class BinanceTradeClient(BaseTradeClient):
    exchange_name = "binance"

    # Size thresholds in USD
    WHALE_THRESHOLD = 1_000_000
    LARGE_THRESHOLD = 100_000
    MEDIUM_THRESHOLD = 10_000
    SMALL_THRESHOLD = 1_000

    def __init__(self, coins: list[str], on_event):
        super().__init__(coins, on_event)
        # Binance uses /ws/ URL with streams separated by /
        streams = "/".join(f"{coin.lower()}usdt@aggTrade" for coin in coins)
        self.ws_url = f"wss://fstream.binance.com/ws/{streams}"

    def get_subscribe_message(self) -> dict | None:
        # Binance combined stream doesn't need subscription message
        # Streams are already specified in the URL
        return None

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
        # With /ws/ URL format, messages come raw (no wrapper)
        # Check if this is an aggTrade message
        if data.get("e") != "aggTrade":
            return None

        # Extract coin from symbol (e.g., "BTCUSDT" -> "BTC")
        symbol = data.get("s", "")
        coin = symbol.replace("USDT", "").replace("PERP", "")

        # IMPORTANT: m=true means buyer is maker = SELL taker
        # m=false means seller is maker = BUY taker
        is_buyer_maker = data.get("m", False)
        side = "sell" if is_buyer_maker else "buy"

        price = float(data.get("p", 0))
        quantity = float(data.get("q", 0))
        value_usd = quantity * price

        # Filter out trades below $1,000
        if value_usd < self.SMALL_THRESHOLD:
            return None

        return TradeEvent(
            exchange=self.exchange_name,
            coin=coin,
            side=side,
            size=quantity,
            price=price,
            value_usd=value_usd,
            timestamp=data.get("T", 0),
            size_category=self._classify_size(value_usd),
        )
