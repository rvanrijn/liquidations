from src.liqhunt.models import Signal, SweepResult, Candle
from src.liqlevels.models import LiqSnapshot
from src.radar.builder import build_state


def _snapshot(long_usd=2.0, short_usd=1.0, price=61500.0):
    return LiqSnapshot(btc_price=price, total_long_usd=long_usd,
                       total_short_usd=short_usd, timestamp=123.0)


def test_build_state_standby_when_no_signal():
    st = build_state(
        snapshot=_snapshot(), signal=None, rejection_reason="Imbalance insufficient",
        price=61500.0, oi_delta_pct=-0.4, oi_velocity=-0.07, cvd_30m=-1.0e6,
        funding=0.0001, taker_ratio=1.1, quality=0.3, range_6h=1800.0,
        liq_levels_long=[(60950.0, 4.2e6)], liq_levels_short=[(62380.0, 2.1e6)],
        connections={"liq_feed": True, "trade_feed": True},
    )
    assert st.verdict == "none"
    assert st.signal is None
    assert st.rejection_reason == "Imbalance insufficient"
    assert st.bigger_side == "LONG"
    assert st.imbalance == _snapshot().imbalance_ratio
    assert st.magnets["long"] == [(60950.0, 4.2e6)]


def test_build_state_armed_maps_signal_fields():
    sweep = SweepResult(valid=True, candle=Candle(1, 2, 0, 1, 10, 0),
                        extreme=60000.0, direction="DOWN")
    sig = Signal(direction="LONG", entry_price=61000.0, stop_price=60500.0,
                 risk_pct=0.01, primary_target=62000.0, secondary_target=62500.0,
                 reasoning="sweep", magnet_side="LONG", imbalance_ratio=1.5,
                 sweep=sweep, quality_score=0.7)
    st = build_state(
        snapshot=_snapshot(), signal=sig, rejection_reason="",
        price=61000.0, oi_delta_pct=-0.5, oi_velocity=-0.1, cvd_30m=-2.0e6,
        funding=0.0, taker_ratio=1.0, quality=0.7, range_6h=2000.0,
        liq_levels_long=[], liq_levels_short=[],
        connections={"liq_feed": True, "trade_feed": True},
    )
    assert st.verdict == "ARMED"
    assert st.signal == {"direction": "LONG", "entry": 61000.0,
                         "stop": 60500.0, "target": 62000.0}
    assert st.quality == 0.7


def test_build_state_null_when_no_snapshot():
    st = build_state(
        snapshot=None, signal=None, rejection_reason="No magnet snapshot",
        price=61000.0, oi_delta_pct=0.0, oi_velocity=0.0, cvd_30m=0.0,
        funding=0.0, taker_ratio=1.0, quality=0.0, range_6h=0.0,
        liq_levels_long=[], liq_levels_short=[],
        connections={"liq_feed": False, "trade_feed": False},
    )
    assert st.imbalance is None
    assert st.bigger_side is None
    assert st.magnets == {"long": [], "short": []}
