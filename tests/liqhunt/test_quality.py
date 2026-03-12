"""Tests for sigmoid quality gate."""

from src.liqhunt.models import Signal, SweepResult, Candle


def test_signal_has_quality_fields():
    """Signal dataclass includes quality_score, gate_probability, notional_scale."""
    candle = Candle(open=100, high=105, low=95, close=102, volume=1000, timestamp=0)
    sweep = SweepResult(valid=True, candle=candle, extreme=95.0, direction="DOWN", criteria_met=["range"])
    sig = Signal(
        direction="SHORT", entry_price=100, stop_price=101,
        risk_pct=0.01, primary_target=99, secondary_target=98,
        reasoning="test", magnet_side="LONG", imbalance_ratio=2.0, sweep=sweep,
    )
    assert sig.quality_score == 0.0
    assert sig.gate_probability == 0.0
    assert sig.notional_scale == 1.0


import math
from src.liqhunt.quality import compute_quality_score


def test_quality_score_all_zeros():
    """All-zero/neutral inputs produce near-zero quality score.

    taker_ratio=1.0 with LONG magnet (ideal=0.7) gives norm_taker=0.4,
    so the score is W_TAKER * 0.4 / WEIGHT_SUM ≈ 0.038.
    """
    q = compute_quality_score(
        oi_change_pct=0.0, imbalance_ratio=1.0, funding_rate=0.0,
        price_range_6h=0.0, oi_velocity=0.0, taker_ratio=1.0,
        magnet_side="LONG",
    )
    assert abs(q - 0.04 / 1.05) < 1e-9


def test_quality_score_perfect_short():
    """Strong OI drop + high imbalance + aligned funding → high quality."""
    q = compute_quality_score(
        oi_change_pct=-1.0, imbalance_ratio=3.0, funding_rate=0.01,
        price_range_6h=3000.0, oi_velocity=-0.2, taker_ratio=0.7,
        magnet_side="LONG",
    )
    assert q > 0.8


def test_quality_score_bounded_zero_one():
    """Quality score is always in [0, 1]."""
    q = compute_quality_score(
        oi_change_pct=-5.0, imbalance_ratio=10.0, funding_rate=0.1,
        price_range_6h=10000.0, oi_velocity=-1.0, taker_ratio=0.3,
        magnet_side="LONG",
    )
    assert 0.0 <= q <= 1.0


def test_quality_score_taker_alignment_short_trade():
    """For LONG magnet (SHORT trade), taker_ratio ~0.7 is ideal."""
    q_good = compute_quality_score(
        oi_change_pct=-0.5, imbalance_ratio=2.0, funding_rate=0.0,
        price_range_6h=2000.0, oi_velocity=-0.1, taker_ratio=0.7,
        magnet_side="LONG",
    )
    q_bad = compute_quality_score(
        oi_change_pct=-0.5, imbalance_ratio=2.0, funding_rate=0.0,
        price_range_6h=2000.0, oi_velocity=-0.1, taker_ratio=1.3,
        magnet_side="LONG",
    )
    assert q_good > q_bad


def test_quality_score_funding_alignment():
    """LONG magnet + positive funding = aligned (longs paying = crowded longs)."""
    q_aligned = compute_quality_score(
        oi_change_pct=-0.5, imbalance_ratio=2.0, funding_rate=0.01,
        price_range_6h=2000.0, oi_velocity=-0.1, taker_ratio=1.0,
        magnet_side="LONG",
    )
    q_opposed = compute_quality_score(
        oi_change_pct=-0.5, imbalance_ratio=2.0, funding_rate=-0.01,
        price_range_6h=2000.0, oi_velocity=-0.1, taker_ratio=1.0,
        magnet_side="LONG",
    )
    assert q_aligned > q_opposed