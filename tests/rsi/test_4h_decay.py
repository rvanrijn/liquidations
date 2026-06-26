"""Tests for the pure decay-line decision logic (_decay_status)."""
import numpy as np
from rsi_4h_forward import _decay_status

DAY = 86_400_000  # ms


def test_at_equity_highs_is_healthy():
    eq = np.array([5000.0, 5100.0, 5200.0])      # last point is the max
    tt = np.array([0, DAY, 2 * DAY])
    days, crossed, msg = _decay_status(eq, tt, now_ms=3 * DAY, line_days=394)
    assert days == 0.0 and crossed is False and "healthy" in msg.lower()


def test_in_drawdown_within_line_counts_down():
    eq = np.array([5000.0, 5500.0, 5300.0])      # ATH at index 1, now underwater
    tt = np.array([0, 100 * DAY, 200 * DAY])
    days, crossed, msg = _decay_status(eq, tt, now_ms=300 * DAY, line_days=394)
    assert not crossed and abs(days - 200) < 1 and "to line" in msg


def test_drawdown_beyond_line_flags_decay():
    eq = np.array([5000.0, 5500.0, 5300.0])      # ATH at index 1
    tt = np.array([0, 100 * DAY, 200 * DAY])
    days, crossed, msg = _decay_status(eq, tt, now_ms=600 * DAY, line_days=394)  # 500d underwater
    assert crossed and days > 394 and "DECAY" in msg.upper()
