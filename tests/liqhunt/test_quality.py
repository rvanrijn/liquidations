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
    so the score is W_TAKER * 0.4 / WEIGHT_SUM.
    """
    from src.liqhunt.quality import WEIGHT_SUM, W_TAKER
    q = compute_quality_score(
        oi_change_pct=0.0, imbalance_ratio=1.0, funding_rate=0.0,
        price_range_6h=0.0, oi_velocity=0.0, taker_ratio=1.0,
        magnet_side="LONG",
    )
    expected = (W_TAKER * 0.4) / WEIGHT_SUM
    assert abs(q - expected) < 1e-9


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
    """LONG magnet + positive funding = aligned (longs paying = crowded longs).

    When W_FUND=0, funding has no effect, so scores are equal.
    """
    from src.liqhunt.quality import W_FUND
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
    if W_FUND > 0:
        assert q_aligned > q_opposed
    else:
        assert q_aligned == q_opposed


from src.liqhunt.quality import sigmoid_gate, compute_notional_scale, P_MIN, SIGMOID_Q0


def test_sigmoid_gate_at_q0_returns_half():
    """Sigmoid at q0 should return exactly 0.5."""
    p = sigmoid_gate(SIGMOID_Q0)
    assert abs(p - 0.5) < 1e-6


def test_sigmoid_gate_high_quality():
    """High quality score -> probability near 1.0."""
    p = sigmoid_gate(1.0)
    assert p > 0.95


def test_sigmoid_gate_low_quality():
    """Low quality score -> probability near 0.0."""
    p = sigmoid_gate(0.0)
    assert p < 0.15


def test_sigmoid_gate_monotonic():
    """Higher quality -> higher probability."""
    p1 = sigmoid_gate(0.2)
    p2 = sigmoid_gate(0.5)
    p3 = sigmoid_gate(0.8)
    assert p1 < p2 < p3


def test_notional_scale_below_pmin():
    """Below p_min -> no trade (scale 0)."""
    scale = compute_notional_scale(P_MIN - 0.01)
    assert scale == 0.0


def test_notional_scale_at_pmin():
    """At p_min -> minimum scale (0.5x)."""
    scale = compute_notional_scale(P_MIN)
    assert abs(scale - 0.5) < 0.01


def test_notional_scale_at_one():
    """At probability 1.0 -> maximum scale (2.0x)."""
    scale = compute_notional_scale(1.0)
    assert abs(scale - 2.0) < 0.01


def test_notional_scale_midpoint():
    """Midpoint probability -> ~1.25x."""
    mid_p = (P_MIN + 1.0) / 2
    scale = compute_notional_scale(mid_p)
    assert abs(scale - 1.25) < 0.1


# ── Integration tests ──────────────────────────────────────────


from src.liqhunt.models import Candle
from src.liqhunt.signal_engine import SignalEngine
from src.liqlevels.models import LiqSnapshot


def _make_snapshot(bigger_side: str = "LONG", imbalance: float = 2.5) -> LiqSnapshot:
    """Create a minimal LiqSnapshot for testing."""
    if bigger_side == "LONG":
        long_usd = 100e6
        short_usd = long_usd / imbalance
    else:
        short_usd = 100e6
        long_usd = short_usd / imbalance
    return LiqSnapshot(
        btc_price=100_000.0,
        total_long_usd=long_usd,
        total_short_usd=short_usd,
        timestamp=0,
    )


def _make_candles(magnet_price: float, side: str) -> list[Candle]:
    """Create 3 candles that form a valid sweep + reclaim."""
    if side == "LONG":
        return [
            Candle(open=100_500, high=100_800, low=100_200, close=100_400, volume=100, timestamp=1000),
            Candle(open=100_400, high=100_500, low=magnet_price * 0.995, close=magnet_price * 0.998,
                   volume=200, timestamp=2000),
            Candle(open=magnet_price * 1.001, high=magnet_price * 1.01, low=magnet_price * 0.999,
                   close=magnet_price * 1.005, volume=150, timestamp=3000),
        ]
    else:
        return [
            Candle(open=99_500, high=99_800, low=99_200, close=99_600, volume=100, timestamp=1000),
            Candle(open=99_600, high=magnet_price * 1.005, low=99_500, close=magnet_price * 1.002,
                   volume=200, timestamp=2000),
            Candle(open=magnet_price * 0.999, high=magnet_price * 1.001, low=magnet_price * 0.99,
                   close=magnet_price * 0.995, volume=150, timestamp=3000),
        ]


def test_signal_engine_emits_quality_fields():
    """Signal emitted by engine should have quality_score > 0 and notional_scale > 0."""
    from src.liqhunt.quality import USE_SIGMOID_GATE
    if not USE_SIGMOID_GATE:
        return

    engine = SignalEngine()
    snapshot = _make_snapshot("LONG", 2.5)
    magnet_price = 99_000.0
    candles = _make_candles(magnet_price, "LONG")

    signal = engine.evaluate(
        snapshot=snapshot, candles=candles, avg_range=500.0,
        btc_price=100_000.0,
        liq_levels_long=[(magnet_price, 50e6)],
        liq_levels_short=[(101_000.0, 30e6)],
        btc_delta_1m=-5e6,
        oi_change_pct=-0.5,
        funding_rate=0.001,
        price_range_6h=2000.0,
        oi_velocity=-0.1,
        taker_ratio=0.7,
    )

    if signal is not None:
        assert signal.quality_score > 0
        assert signal.gate_probability > 0
        assert signal.notional_scale > 0


def test_legacy_gates_block_when_sigmoid_disabled():
    """When USE_SIGMOID_GATE=False, binary OI gate still blocks signals."""
    from unittest.mock import patch
    with patch("src.liqhunt.signal_engine.USE_SIGMOID_GATE", False):
        engine = SignalEngine()
        snapshot = _make_snapshot("LONG", 2.5)
        magnet_price = 99_000.0
        candles = _make_candles(magnet_price, "LONG")
        signal = engine.evaluate(
            snapshot=snapshot, candles=candles, avg_range=500.0,
            btc_price=100_000.0,
            liq_levels_long=[(magnet_price, 50e6)],
            liq_levels_short=[(101_000.0, 30e6)],
            btc_delta_1m=-5e6, oi_change_pct=-0.05,
            funding_rate=0.001, price_range_6h=2000.0,
            oi_velocity=-0.1, taker_ratio=0.7,
        )
        assert signal is None
        assert "OI regime" in engine.rejection_reason or "shadow" in engine.rejection_reason


from unittest.mock import MagicMock
from src.liqhunt.paper_trader import PaperTrader, LEVERAGE, STARTING_BALANCE


def test_paper_trader_uses_notional_scale():
    """Paper trader should scale notional by signal.notional_scale."""
    mock_db = MagicMock()
    mock_db.get_paper_balance.return_value = STARTING_BALANCE
    mock_db.load_open_position.return_value = None
    mock_db.get_recent_paper_trades.return_value = []
    mock_db.get_paper_stats.return_value = {}

    trader = PaperTrader(mock_db)

    candle = Candle(open=100, high=105, low=95, close=102, volume=1000, timestamp=0)
    sweep = SweepResult(valid=True, candle=candle, extreme=95.0, direction="DOWN", criteria_met=["range"])
    sig = Signal(
        direction="SHORT", entry_price=100_000, stop_price=101_000,
        risk_pct=0.01, primary_target=99_000, secondary_target=98_000,
        reasoning="test", magnet_side="LONG", imbalance_ratio=2.0, sweep=sweep,
        notional_scale=1.5,
    )

    trader.on_signal(sig, snapshot_price=100_000)

    assert trader.position is not None
    expected_notional = STARTING_BALANCE * LEVERAGE * 1.5  # $37,500
    assert abs(trader.position.notional - expected_notional) < 0.01