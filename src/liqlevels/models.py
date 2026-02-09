# src/liqlevels/models.py
"""Data models for the liquidation magnet monitor."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LiqSnapshot:
    """Frozen snapshot of liquidation levels at a point in time."""

    btc_price: float
    nearest_long_price: float  # Nearest long liq price (below current)
    nearest_short_price: float  # Nearest short liq price (above current)
    total_long_usd: float  # Total estimated USD in long liqs
    total_short_usd: float  # Total estimated USD in short liqs
    timestamp: float  # time.time()

    @property
    def bigger_side(self) -> str:
        """Which side has more USD at risk."""
        return "LONG" if self.total_long_usd >= self.total_short_usd else "SHORT"

    @property
    def imbalance_ratio(self) -> float:
        """Ratio of bigger side to smaller side."""
        bigger = max(self.total_long_usd, self.total_short_usd)
        smaller = min(self.total_long_usd, self.total_short_usd)
        if smaller == 0:
            return float("inf")
        return bigger / smaller


@dataclass
class Battle:
    """A resolved magnet battle."""

    bigger_side: str  # "LONG" or "SHORT"
    hit_side: str  # "LONG" or "SHORT" — which side price swept first
    hypothesis_correct: bool  # Did price hit the bigger side?
    imbalance_ratio: float
    duration_seconds: float
    snapshot_price: float
    nearest_long_price: float
    nearest_short_price: float
    total_long_usd: float
    total_short_usd: float
    resolved_price: float
    timestamp: float  # When battle was resolved
    id: int | None = None
