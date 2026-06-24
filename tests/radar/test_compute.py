from collections import deque

from src.radar.compute import (
    oi_change_pct,
    oi_velocity,
    cvd_sum,
    price_range_6h,
    liq_side,
)


def test_oi_change_pct_drop_from_peak():
    hist = deque([100.0, 110.0, 99.0])  # peak 110 -> now 99
    assert oi_change_pct(hist) == (99.0 - 110.0) / 110.0 * 100


def test_oi_change_pct_insufficient_history():
    assert oi_change_pct(deque([100.0])) == 0.0
    assert oi_change_pct(deque()) == 0.0


def test_oi_velocity_needs_12_samples():
    assert oi_velocity(deque([100.0] * 11)) == 0.0
    hist = deque([100.0] * 12)
    hist[-1] = 98.0  # last sample drops vs hist[-12]=100
    # (98 - 100)/100 * 100 / 2
    assert oi_velocity(hist) == (98.0 - 100.0) / 100.0 * 100 / 2


def test_cvd_sum():
    assert cvd_sum(deque([1.0, -2.0, 3.5])) == 2.5
    assert cvd_sum(deque()) == 0.0


def test_price_range_6h_trims_old_points():
    now = 1_000_000.0
    pts = deque([(now - 30000, 100.0), (now - 100, 105.0), (now, 103.0)])
    # first point is older than 21600s -> trimmed; range over {105,103} = 2.0
    assert price_range_6h(pts, now=now) == 2.0


def test_price_range_6h_insufficient():
    assert price_range_6h(deque(), now=1.0) == 0.0


def test_liq_side_mapping():
    assert liq_side("SELL") == "long"   # a long was liquidated
    assert liq_side("BUY") == "short"   # a short was liquidated
