"""VWAP (Volume Weighted Average Price) calculator for scalping bot.

Rolling 5-minute VWAP: sum(price * volume) / sum(volume) over the last 5 minutes.
"""

from collections import deque
from time import time


class VWAPCalculator:
    """Calculate rolling 5-minute VWAP from trade ticks."""

    def __init__(self, window_ms: int = 5 * 60 * 1000):
        self._window_ms = window_ms
        self._ticks: deque[tuple[int, float, float]] = deque()  # (ts_ms, price, volume)

    def update(self, price: float, volume: float) -> None:
        """Add a trade tick.

        Args:
            price: Trade price
            volume: Trade volume
        """
        now_ms = int(time() * 1000)
        self._ticks.append((now_ms, price, volume))
        cutoff = now_ms - self._window_ms
        while self._ticks and self._ticks[0][0] < cutoff:
            self._ticks.popleft()

    @property
    def vwap(self) -> float:
        """Current rolling VWAP. Returns 0.0 if no data."""
        if not self._ticks:
            return 0.0
        cum_pv = 0.0
        cum_vol = 0.0
        for _, p, v in self._ticks:
            cum_pv += p * v
            cum_vol += v
        if cum_vol == 0.0:
            return 0.0
        return cum_pv / cum_vol
