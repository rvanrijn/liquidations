# src/liqlevels/models.py
"""Data models for the liquidation magnet monitor."""

from dataclasses import dataclass


# Battle resolves when price moves this % from snapshot in either direction
RESOLVE_MOVE_PCT = 0.5


@dataclass(frozen=True)
class LiqSnapshot:
    """Frozen snapshot of liquidation imbalance at a point in time."""

    btc_price: float
    total_long_usd: float
    total_short_usd: float
    timestamp: float  # time.time()
    open_interest_usd: float = 0.0

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

    bigger_side: str  # "LONG" or "SHORT" — side with more USD at risk
    moved_side: str  # "LONG" or "SHORT" — which side price moved toward first
    hypothesis_correct: bool  # Did price move toward the bigger side?
    imbalance_ratio: float
    duration_seconds: float
    snapshot_price: float
    resolved_price: float
    move_pct: float  # How far price moved from snapshot (signed)
    total_long_usd: float
    total_short_usd: float
    timestamp: float  # When battle was resolved
    id: int | None = None
    oi_start_usd: float = 0.0
    oi_end_usd: float = 0.0
    oi_change_pct: float = 0.0
