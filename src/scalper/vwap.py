"""VWAP (Volume Weighted Average Price) calculator for scalping bot.

The VWAP is calculated as: sum(price * volume) / sum(volume)
It resets at 00:00 UTC daily.
"""

from datetime import date, datetime, timezone


class VWAPCalculator:
    """Calculate VWAP with automatic daily reset at midnight UTC."""

    def __init__(self):
        self._cum_pv: float = 0.0  # cumulative price * volume
        self._cum_volume: float = 0.0  # cumulative volume
        self._last_reset_date: date | None = None

    def update(self, price: float, volume: float) -> None:
        """Add a trade to VWAP calculation. Auto-resets at midnight UTC.

        Args:
            price: Trade price
            volume: Trade volume
        """
        # Check if we need to reset (new UTC day)
        current_date = datetime.now(timezone.utc).date()

        if self._last_reset_date is None:
            self._last_reset_date = current_date
        elif current_date > self._last_reset_date:
            self.reset()
            self._last_reset_date = current_date

        # Accumulate price * volume and volume
        self._cum_pv += price * volume
        self._cum_volume += volume

    @property
    def vwap(self) -> float:
        """Current VWAP value. Returns 0.0 if no data.

        Returns:
            Current VWAP or 0.0 if no volume accumulated
        """
        if self._cum_volume == 0.0:
            return 0.0
        return self._cum_pv / self._cum_volume

    def reset(self) -> None:
        """Reset VWAP accumulation."""
        self._cum_pv = 0.0
        self._cum_volume = 0.0
