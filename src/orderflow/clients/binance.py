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
        # Binance uses combined stream URL for aggregate trades
        streams = ",".join(f"{coin.lower()}usdt@aggTrade" for coin in coins)
        self.ws_url = f"wss://fstream.binance.com/stream?streams={streams}"

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
        # Check if this is an aggTrade message
        if "data" not in data:
            return None

        trade_data = data["data"]
        if trade_data.get("e") != "aggTrade":
            return None

        # Extract coin from symbol (e.g., "BTCUSDT" -> "BTC")
        symbol = trade_data.get("s", "")
        coin = symbol.replace("USDT", "").replace("PERP", "")

        # IMPORTANT: m=true means buyer is maker = SELL taker
        # m=false means seller is maker = BUY taker
        is_buyer_maker = trade_data.get("m", False)
        side = "sell" if is_buyer_maker else "buy"

        price = float(trade_data.get("p", 0))
        quantity = float(trade_data.get("q", 0))
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
            timestamp=trade_data.get("T", 0),
            size_category=self._classify_size(value_usd),
        )
