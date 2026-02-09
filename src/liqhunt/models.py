# src/liqhunt/models.py
"""Data models for the liquidation hunt signal engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass
class Candle:
    """A single 5-minute OHLCV candle."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: int  # open time in ms

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def wick_ratio(self) -> float:
        """Max wick as fraction of total range."""
        if self.range == 0:
            return 0.0
        return max(self.upper_wick, self.lower_wick) / self.range

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2


@dataclass
class SweepResult:
    """Result of sweep validation."""

    valid: bool
    candle: Candle
    extreme: float  # wick tip (lowest low or highest high)
    direction: str  # "DOWN" or "UP"
    criteria_met: list[str] = field(default_factory=list)


@dataclass
class Signal:
    """A trade signal output."""

    direction: str  # "LONG" or "SHORT"
    entry_price: float
    stop_price: float
    risk_pct: float
    primary_target: float
    secondary_target: float
    reasoning: str
    magnet_side: str
    imbalance_ratio: float
    sweep: SweepResult
    timestamp: float = field(default_factory=time)
