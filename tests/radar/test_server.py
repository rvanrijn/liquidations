from fastapi.testclient import TestClient

from src.radar.runner import RadarRunner
from src.radar.server import make_app
from src.radar.state import RadarState, liq_message


def _state():
    return RadarState(
        price=61500.0, magnets={"long": [], "short": []},
        oi_delta_pct=0.0, oi_velocity=0.0, cvd_30m=0.0, funding=0.0,
        imbalance=None, bigger_side=None, taker_ratio=1.0, quality=0.0,
        range_6h=0.0, verdict="none", rejection_reason="warming up",
        signal=None, connections={"liq_feed": False, "trade_feed": False},
    )


def test_healthz():
    app = make_app(RadarRunner(engine=object()), start_loop=False)
    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}


def test_ws_sends_initial_state_then_broadcasts_liq():
    runner = RadarRunner(engine=object())
    runner.state = _state()
    app = make_app(runner, start_loop=False)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "state"
        assert first["rejection_reason"] == "warming up"
        # simulate the runner emitting a liquidation
        app.state.manager.broadcast_sync(liq_message(1.0, "long", 5000.0, 61000.0))
        msg = ws.receive_json()
        assert msg["type"] == "liq"
        assert msg["side"] == "long"
