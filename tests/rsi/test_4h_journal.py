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


def test_per_asset_split_tracks_live_contribution():
    j = Journal()
    j.record("EXIT", {"kind": "live", "asset": "BTC/USDT", "pnl": -200.0})
    j.record("EXIT", {"kind": "live", "asset": "BTC/USDT", "pnl": 50.0})
    j.record("EXIT", {"kind": "live", "asset": "ETH/USDT", "pnl": 400.0})
    j.record("EXIT", {"kind": "shadow", "asset": "BTC/USDT", "pnl": 999.0})  # shadow excluded
    assert j.by_asset["BTC/USDT"]["trades"] == 2 and j.by_asset["BTC/USDT"]["wins"] == 1
    assert abs(j.by_asset["BTC/USDT"]["net"] - (-150.0)) < 1e-9
    assert j.by_asset["ETH/USDT"]["trades"] == 1 and abs(j.by_asset["ETH/USDT"]["net"] - 400.0) < 1e-9
    s = j.summary_line()
    assert "BTC/USDT" in s and "ETH/USDT" in s


def test_per_asset_survives_state_roundtrip(tmp_path):
    sp = tmp_path / "s.json"
    j = Journal(state_path=sp); b = PaperBook()
    j.record("EXIT", {"kind": "live", "asset": "ETH/USDT", "pnl": 400.0})
    j.save_state(b, last_ts={})
    j2 = Journal(state_path=sp); b2 = PaperBook()
    j2.load_state(b2)
    assert j2.by_asset["ETH/USDT"]["trades"] == 1 and abs(j2.by_asset["ETH/USDT"]["net"] - 400.0) < 1e-9


def test_exit_without_asset_still_works():
    # existing aggregate-only EXIT records (no 'asset') must not crash per-asset tracking
    j = Journal()
    j.record("EXIT", {"kind": "live", "pnl": 120.0})
    assert j.live_trades == 1 and j.by_asset == {}


def test_summary_shows_shadow_minus_live_gap():
    j = Journal()
    j.record("EXIT", {"kind": "live", "asset": "BTC/USDT", "pnl": 100.0})
    j.record("EXIT", {"kind": "shadow", "asset": "BTC/USDT", "pnl": 380.0})
    s = j.summary_line()
    assert "hold48" in s and "+280" in s   # hold-48 Δ vs live = 380 - 100 = +280


def test_ladder_counter_and_summary():
    j = Journal()
    j.record("EXIT", {"kind": "live", "asset": "BTC/USDT", "pnl": 100.0})
    j.record("EXIT", {"kind": "ladder", "pnl": 260.0})
    j.record("EXIT", {"kind": "ladder", "pnl": -40.0})
    assert j.ladder_trades == 2 and abs(j.ladder_net - 220.0) < 1e-9
    s = j.summary_line()
    assert "ladder" in s and "+120" in s   # ladder Δ vs live = 220 - 100 = +120


def test_regime_counter_summary_and_state_roundtrip(tmp_path):
    sp = tmp_path / "s.json"
    j = Journal(state_path=sp); b = PaperBook()
    j.record("EXIT", {"kind": "live", "asset": "BTC/USDT", "pnl": 100.0})
    j.record("EXIT", {"kind": "regime", "pnl": 180.0})
    j.record("EXIT", {"kind": "regime", "pnl": -30.0})
    assert j.regime_trades == 2 and j.regime_wins == 1 and abs(j.regime_net - 150.0) < 1e-9
    s = j.summary_line()
    assert "regime" in s and "+50" in s    # regime Δ vs live = 150 - 100 = +50
    j.save_state(b, last_ts={})
    j2 = Journal(state_path=sp); b2 = PaperBook(); j2.load_state(b2)
    assert j2.regime_trades == 2 and abs(j2.regime_net - 150.0) < 1e-9
