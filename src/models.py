from dataclasses import dataclass


@dataclass
class LiquidationEvent:
    exchange: str       # "bybit" | "binance"
    coin: str           # "BTC", "ETH", etc.
    side: str           # "long" | "short"
    size: float         # Position size
    price: float        # Liquidation price
    value_usd: float    # Total USD value
    timestamp: int      # Unix ms
    wallet: str | None  # Address if available
