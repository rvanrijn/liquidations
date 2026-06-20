import json

from src.radar.state import RadarState, liq_message


def test_radar_state_to_dict_is_json_safe_and_tagged():
    s = RadarState(
        price=61540.0,
        magnets={"long": [(60950.0, 4.2e6)], "short": [(62380.0, 2.1e6)]},
        oi_delta_pct=-0.41, oi_velocity=-0.07, cvd_30m=-1.2e6,
        funding=0.0001, imbalance=1.42, bigger_side="LONG",
        taker_ratio=1.08, quality=0.62, range_6h=1850.0,
        verdict="none", rejection_reason="warming up",
        signal=None,
        connections={"liq_feed": True, "trade_feed": True},
    )
    d = s.to_dict()
    assert d["type"] == "state"
    assert d["price"] == 61540.0
    assert d["bigger_side"] == "LONG"
    # magnets serialize as lists of [price, usd], not tuples
    assert d["magnets"]["long"] == [[60950.0, 4.2e6]]
    # must round-trip through JSON
    json.dumps(d)


def test_radar_state_armed_includes_signal_fields():
    s = RadarState(
        price=61540.0, magnets={"long": [], "short": []},
        oi_delta_pct=0.0, oi_velocity=0.0, cvd_30m=0.0, funding=0.0,
        imbalance=2.0, bigger_side="SHORT", taker_ratio=1.0, quality=0.8,
        range_6h=2000.0, verdict="ARMED", rejection_reason="",
        signal={"direction": "SHORT", "entry": 61500.0, "stop": 61900.0,
                "target": 60800.0},
        connections={"liq_feed": True, "trade_feed": True},
    )
    d = s.to_dict()
    assert d["verdict"] == "ARMED"
    assert d["signal"]["direction"] == "SHORT"
    json.dumps(d)


def test_radar_state_null_snapshot_fields():
    s = RadarState(
        price=61540.0, magnets={"long": [], "short": []},
        oi_delta_pct=0.0, oi_velocity=0.0, cvd_30m=0.0, funding=0.0,
        imbalance=None, bigger_side=None, taker_ratio=1.0, quality=0.0,
        range_6h=0.0, verdict="none", rejection_reason="No magnet snapshot",
        signal=None, connections={"liq_feed": False, "trade_feed": False},
    )
    d = s.to_dict()
    assert d["imbalance"] is None
    assert d["bigger_side"] is None
    assert d["magnets"] == {"long": [], "short": []}
    json.dumps(d)


def test_liq_message_shape():
    m = liq_message(ts=1750000000.0, side="long", usd=125000.0, price=61530.0)
    assert m == {"type": "liq", "ts": 1750000000.0, "side": "long",
                 "usd": 125000.0, "price": 61530.0}
    json.dumps(m)
