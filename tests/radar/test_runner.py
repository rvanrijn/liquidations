from collections import deque
from types import SimpleNamespace

from src.liqlevels.models import LiqSnapshot
from src.radar.runner import RadarRunner


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


def test_build_once_is_read_only_no_exceptions_on_repeat():
    r = RadarRunner(engine=_FakeEngine())
    for i in range(15):
        st = r.build_once(_market(), now=1000.0 + i * 10)
    # after 12+ samples oi_velocity is computable (still 0 here, constant OI)
    assert st.oi_velocity == 0.0
