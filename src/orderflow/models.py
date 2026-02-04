from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True)
class TradeEvent:
    """Represents a single trade event from an exchange."""

    exchange: str  # e.g., "bybit", "binance"
    coin: str  # e.g., "BTC", "ETH"
    side: Literal["buy", "sell"]
    size: float  # quantity
    price: float
    value_usd: float  # size * price
    timestamp: int  # Unix milliseconds
    size_category: Literal["whale", "large", "medium", "small"]
