from collections import deque
from types import SimpleNamespace

from src.liqhunt.models import Candle, Signal, SweepResult
from src.liqlevels.models import LiqSnapshot
from src.radar.runner import RadarRunner, build_snapshot


class _FakeEngine:
    """Stands in for SignalEngine: returns a preset verdict + reason."""
    def __init__(self, signal=None, reason="No magnet snapshot"):
        self._signal = signal
        self.rejection_reason = reason

    def evaluate(self, **kwargs):
        return self._signal


def _market(price=61500.0, snapshot=None):
    return SimpleNamespace(
        price=price,
        snapshot=snapshot,
        candles=[],
        avg_range=500.0,
        liq_levels_long=[(60950.0, 4.2e6)],
        liq_levels_short=[(62380.0, 2.1e6)],
        funding=0.0001,
        taker_ratio=1.1,
        quality=0.3,
        liq_long_usd=0.0,
        liq_short_usd=0.0,
        oi_usd=1.0e9,
        cvd_1m=-50000.0,
        connections={"liq_feed": True, "trade_feed": True},
    )


def test_build_once_standby_without_snapshot():
    r = RadarRunner(engine=_FakeEngine(signal=None, reason="No magnet snapshot"))
    state = r.build_once(_market(snapshot=None), now=1000.0)
    assert state.verdict == "none"
    assert state.bigger_side is None
    assert state.rejection_reason == "No magnet snapshot"


def test_build_once_accumulates_oi_and_cvd_history():
    r = RadarRunner(engine=_FakeEngine())
    snap = LiqSnapshot(btc_price=61500.0, total_long_usd=2.0,
                       total_short_usd=1.0, timestamp=1.0)
    # Feed two cycles; OI deque should grow and oi_delta reflect a drop.
    r.build_once(_market(snapshot=snap), now=1000.0)  # oi 1.0e9
    m2 = _market(snapshot=snap)
    m2.oi_usd = 0.99e9                                 # OI dropped
    state = r.build_once(m2, now=1010.0)
    assert state.oi_delta_pct < 0
    assert state.bigger_side == "LONG"
    assert state.imbalance == snap.imbalance_ratio


def test_build_once_stable_under_repeated_calls_with_constant_oi():
    r = RadarRunner(engine=_FakeEngine())
    for i in range(15):
        st = r.build_once(_market(), now=1000.0 + i * 10)
    # after 12+ samples oi_velocity is computable (still 0 here, constant OI)
    assert st.oi_velocity == 0.0


def test_build_once_surfaces_signal_quality_score_when_armed():
    """Fix 1: quality must come from signal.quality_score, not market.quality."""
    sweep = SweepResult(valid=True, candle=Candle(1, 2, 0, 1, 10, 0),
                        extreme=60000.0, direction="DOWN")
    sig = Signal(direction="LONG", entry_price=61000.0, stop_price=60500.0,
                 risk_pct=0.01, primary_target=62000.0, secondary_target=62500.0,
                 reasoning="sweep", magnet_side="LONG", imbalance_ratio=1.5,
                 sweep=sweep, quality_score=0.7)

    r = RadarRunner(engine=_FakeEngine(signal=sig, reason=""))
    snap = LiqSnapshot(btc_price=61500.0, total_long_usd=2.0,
                       total_short_usd=1.0, timestamp=1.0)
    state = r.build_once(_market(snapshot=snap), now=1000.0)

    assert state.verdict == "ARMED"
    assert state.quality == 0.7


def _lvl(usd):
    return SimpleNamespace(price=0.0, estimated_usd=usd)


def test_build_snapshot_none_without_btc():
    assert build_snapshot(None, 1.0) is None


def test_build_snapshot_none_without_liq():
    btc = SimpleNamespace(current_price=61500.0, longs_at_risk=[], shorts_at_risk=[])
    assert build_snapshot(btc, 1.0) is None


def test_build_snapshot_derives_side_and_imbalance_no_db():
    btc = SimpleNamespace(current_price=61500.0,
                          longs_at_risk=[_lvl(2.0), _lvl(2.0)],
                          shorts_at_risk=[_lvl(1.0)])
    snap = build_snapshot(btc, 123.0)
    assert snap.total_long_usd == 4.0
    assert snap.total_short_usd == 1.0
    assert snap.bigger_side == "LONG"
    assert snap.imbalance_ratio == 4.0
    assert snap.btc_price == 61500.0


def test_apply_dev_overrides_clears_day_filter():
    import src.liqhunt.signal_engine as se
    from src.radar.runner import apply_dev_overrides
    orig = se.SKIP_DAYS
    try:
        apply_dev_overrides(True)
        assert se.SKIP_DAYS == set()
    finally:
        se.SKIP_DAYS = orig


def test_apply_dev_overrides_noop_when_false():
    import src.liqhunt.signal_engine as se
    from src.radar.runner import apply_dev_overrides
    orig = se.SKIP_DAYS
    apply_dev_overrides(False)
    assert se.SKIP_DAYS == orig


def test_runner_ignore_day_filter_param_overrides_env():
    from src.radar.runner import RadarRunner
    r = RadarRunner(engine=object(), ignore_day_filter=True)
    assert r.ignore_day_filter is True
    r2 = RadarRunner(engine=object(), ignore_day_filter=False)
    assert r2.ignore_day_filter is False


def test_live_quality_nonzero_for_strong_state():
    from src.radar.runner import _live_quality
    from src.liqlevels.models import LiqSnapshot
    snap = LiqSnapshot(btc_price=61500.0, total_long_usd=4.0,
                       total_short_usd=1.0, timestamp=1.0)
    q = _live_quality(snap, oi_delta=-1.0, oi_vel=-0.2, range_6h=3000.0,
                      funding=0.001, taker_ratio=1.2)
    assert 0.0 < q <= 1.0


def test_build_once_shows_live_quality_in_standby():
    from src.liqlevels.models import LiqSnapshot
    r = RadarRunner(engine=_FakeEngine(signal=None, reason="no sweep"))
    snap = LiqSnapshot(btc_price=61500.0, total_long_usd=4.0,
                       total_short_usd=1.0, timestamp=1.0)
    r.build_once(_market(snapshot=snap), now=1000.0)
    m2 = _market(snapshot=snap); m2.oi_usd = 0.98e9
    st = r.build_once(m2, now=1010.0)
    assert st.verdict == "none"      # still STANDBY
    assert st.quality > 0.0          # but quality is live, not 0.0
