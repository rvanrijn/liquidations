# src/scalper/obv_macd.py
"""OBV MACD Indicator - On Balance Volume with MACD using DEMA.

Parameters from TradingView: DEMA 9 26 2 50
- fast_period: 9
- slow_period: 26
- signal_period: 2
- obv_smooth: 50 (smoothing applied to OBV before MACD)

Blue line = MACD line (fast DEMA - slow DEMA of smoothed OBV)
Red line = Signal line (DEMA of MACD line)

LONG when blue > red (bullish volume momentum)
SHORT when blue < red (bearish volume momentum)
"""

from collections import deque
from time import time


class DEMA:
    """Double Exponential Moving Average."""

    def __init__(self, period: int):
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self.ema1: float | None = None
        self.ema2: float | None = None
        self._count = 0

    def update(self, value: float) -> float | None:
        self._count += 1

        if self.ema1 is None:
            self.ema1 = value
            self.ema2 = value
        else:
            self.ema1 = self.alpha * value + (1 - self.alpha) * self.ema1
            self.ema2 = self.alpha * self.ema1 + (1 - self.alpha) * self.ema2

        if self._count < self.period:
            return None

        return 2 * self.ema1 - self.ema2

    @property
    def value(self) -> float | None:
        if self._count < self.period:
            return None
        if self.ema1 is None or self.ema2 is None:
            return None
        return 2 * self.ema1 - self.ema2


class OBVMACDCalculator:
    """OBV MACD Indicator using DEMA smoothing.

    Calculates On Balance Volume, smooths it, then applies MACD.
    """

    def __init__(
        self,
        fast_period: int = 9,
        slow_period: int = 26,
        signal_period: int = 2,
        obv_smooth: int = 50,
        candle_minutes: int = 1,
    ):
        self._obv: float = 0.0
        self._last_candle_close: float | None = None

        # 5m candle accumulation
        self._candle_minutes = candle_minutes
        self._candle_volume: float = 0.0
        self._candle_open: float | None = None
        self._candle_close: float = 0.0
        self._candle_start: int = 0  # epoch seconds

        # Smoothing DEMA for OBV
        self._obv_dema = DEMA(obv_smooth)

        # MACD DEMAs on smoothed OBV
        self._fast_dema = DEMA(fast_period)
        self._slow_dema = DEMA(slow_period)

        # Signal DEMA on MACD line
        self._signal_dema = DEMA(signal_period)

        self.macd_line: float = 0.0
        self.signal_line: float = 0.0
        self.ready: bool = False
        self.candles_closed: int = 0
        self._warmup_total = obv_smooth + slow_period + signal_period
        self._bullish_count: int = 0
        self._bearish_count: int = 0

    def _current_candle_slot(self) -> int:
        """Get the start of the current candle slot in epoch seconds."""
        now = int(time())
        interval = self._candle_minutes * 60
        return now - (now % interval)

    def update(self, price: float, volume: float) -> None:
        """Update with a new trade tick. Aggregates into candles internally.

        Args:
            price: Trade price
            volume: Trade volume (in base currency)
        """
        slot = self._current_candle_slot()

        # New candle? Close the previous one and process it
        if slot != self._candle_start and self._candle_open is not None:
            self._close_candle()
            # Start new candle
            self._candle_start = slot
            self._candle_open = price
            self._candle_close = price
            self._candle_volume = volume
        elif self._candle_open is None:
            # Very first tick
            self._candle_start = slot
            self._candle_open = price
            self._candle_close = price
            self._candle_volume = volume
        else:
            # Accumulate into current candle
            self._candle_close = price
            self._candle_volume += volume

    def _close_candle(self) -> None:
        """Process a completed candle: update OBV then MACD."""
        close = self._candle_close
        vol = self._candle_volume

        # OBV: compare this candle's close to previous candle's close
        if self._last_candle_close is not None:
            if close > self._last_candle_close:
                self._obv += vol
            elif close < self._last_candle_close:
                self._obv -= vol
        self._last_candle_close = close

        self.candles_closed += 1

        # Smooth OBV
        smoothed = self._obv_dema.update(self._obv)
        if smoothed is None:
            return

        # MACD on smoothed OBV
        fast = self._fast_dema.update(smoothed)
        slow = self._slow_dema.update(smoothed)
        if fast is None or slow is None:
            return

        macd = fast - slow
        self.macd_line = macd

        # Signal line
        sig = self._signal_dema.update(macd)
        if sig is None:
            return

        self.signal_line = sig
        self.ready = True

        # Track consecutive histogram direction for confirmation
        histogram = self.macd_line - self.signal_line
        if histogram > 0:
            self._bullish_count += 1
            self._bearish_count = 0
        elif histogram < 0:
            self._bearish_count += 1
            self._bullish_count = 0
        else:
            self._bullish_count = 0
            self._bearish_count = 0

    def update_candle(self, close: float, volume: float) -> None:
        """Process a completed candle directly (for backtesting).

        Bypasses time-based slot logic — treats each call as one closed candle.
        """
        self._candle_close = close
        self._candle_volume = volume
        self._close_candle()

    @property
    def warmup_remaining(self) -> int:
        """Minutes remaining until indicator is ready."""
        remaining = self._warmup_total - self.candles_closed
        return max(0, remaining * self._candle_minutes)

    @property
    def is_bullish(self) -> bool:
        """Blue line above red line — bullish volume momentum."""
        return self.ready and self.macd_line > self.signal_line

    @property
    def is_bearish(self) -> bool:
        """Red line above blue line — bearish volume momentum."""
        return self.ready and self.macd_line < self.signal_line
