import json
from rsi_4h_forward import Journal, PaperBook


def test_counts_live_and_shadow_separately():
    j = Journal()
    j.record("EXIT", {"kind": "live", "pnl": 120.0})
    j.record("EXIT", {"kind": "live", "pnl": -80.0})
    j.record("EXIT", {"kind": "shadow", "pnl": 200.0})
    j.record("SKIP", {})
    assert j.live_trades == 2 and j.live_wins == 1
    assert abs(j.live_net - 40.0) < 1e-9
    assert j.shadow_trades == 1 and abs(j.shadow_net - 200.0) < 1e-9
    assert j.skips == 1


def test_events_appended(tmp_path):
    ep = tmp_path / "e.jsonl"
    j = Journal(events_path=ep, state_path=tmp_path / "s.json")
    j.record("ENTRY", {"asset": "BTC/USDT", "side": "LONG", "price": 100})
    rec = json.loads(ep.read_text().strip())
    assert rec["event"] == "ENTRY" and rec["asset"] == "BTC/USDT"


def test_state_roundtrip_with_book(tmp_path):
    sp = tmp_path / "s.json"
    j = Journal(state_path=sp); b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=3)
    j.record("EXIT", {"kind": "live", "pnl": 50.0})
    j.save_state(b, last_ts={"BTC/USDT": 999})
    j2 = Journal(state_path=sp); b2 = PaperBook()
    last = j2.load_state(b2)
    assert last["BTC/USDT"] == 999
    assert b2.in_position("BTC/USDT")
    assert j2.live_trades == 1 and abs(j2.live_net - 50.0) < 1e-9
