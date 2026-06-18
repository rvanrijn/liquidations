import json
from rsi_paper import Journal


def test_counters_and_fill_rates(tmp_path):
    j = Journal(events_path=tmp_path / "e.jsonl", state_path=tmp_path / "s.json")
    j.record("ARM", {})
    j.record("ARM", {})
    j.record("FILL", {"kind": "ENTRY", "ttf_sec": 12})
    j.record("MISS", {})
    # 2 arms, 1 entry fill -> 50%
    assert j.entry_arms == 2
    assert j.entry_fills == 1
    assert j.entry_fill_rate() == 0.5
    assert j.avg_ttf_sec() == 12


def test_exit_fill_and_missed_to_stop():
    j = Journal(events_path=None, state_path=None)
    j.record("EXIT_ARM", {})
    j.record("EXIT_ARM", {})
    j.record("FILL", {"kind": "EXIT"})
    j.record("FILL", {"kind": "STOP", "missed_exit": True})
    assert j.exit_arms == 2
    assert j.exit_fills == 1
    assert j.missed_exit_to_stop == 1


def test_events_appended_to_jsonl(tmp_path):
    ep = tmp_path / "e.jsonl"
    j = Journal(events_path=ep, state_path=tmp_path / "s.json")
    j.record("SIGNAL", {"rsi": 28.4, "price": 64800})
    lines = ep.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "SIGNAL" and rec["rsi"] == 28.4


def test_state_roundtrip(tmp_path):
    sp = tmp_path / "s.json"
    j = Journal(events_path=None, state_path=sp)
    j.save_state({"balance": 5084.0, "position": None})
    assert j.load_state()["balance"] == 5084.0
