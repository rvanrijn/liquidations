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