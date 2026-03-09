# src/krakenbot/liq_models.py
"""Data models for the liquidation magnet monitor."""

from dataclasses import dataclass

RESOLVE_MOVE_PCT = 0.5


@dataclass(frozen=True)
class LiqSnapshot:
    """Frozen snapshot of liquidation imbalance at a point in time."""

    btc_price: float
    total_long_usd: float
    total_short_usd: float
    timestamp: float
    open_interest_usd: float = 0.0
    funding_rate: float = 0.0

    @property
    def bigger_side(self) -> str:
        return "LONG" if self.total_long_usd >= self.total_short_usd else "SHORT"

    @property
    def imbalance_ratio(self) -> float:
        bigger = max(self.total_long_usd, self.total_short_usd)
        smaller = min(self.total_long_usd, self.total_short_usd)
        if smaller == 0:
            return float("inf")
        return bigger / smaller


@dataclass
class Battle:
    """A resolved magnet battle."""

    bigger_side: str
    moved_side: str
    hypothesis_correct: bool
    imbalance_ratio: float
    duration_seconds: float
    snapshot_price: float
    resolved_price: float
    move_pct: float
    total_long_usd: float
    total_short_usd: float
    timestamp: float
    id: int | None = None
    oi_start_usd: float = 0.0
    oi_end_usd: float = 0.0
    oi_change_pct: float = 0.0
    funding_rate: float = 0.0
    mfe_pct: float = 0.0  # max favorable move during battle (toward magnet)
    mae_pct: float = 0.0  # max adverse move during battle (against magnet)
    ha_color: str = ""
    ha_body_ratio: float = 0.0
    ha_streak: int = 0
