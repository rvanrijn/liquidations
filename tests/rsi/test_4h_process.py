from rsi_4h_forward import PaperBook, Journal, process_asset


def candle(ts, hi, lo, close): return [ts, close, hi, lo, close, 1.0]


def test_entry_on_latest_break(monkeypatch):
    import rsi_4h_forward as m
    cs = [candle(i, 101, 99, 100) for i in range(40)]
    monkeypatch.setattr(m, "latest_break",
        lambda c: {"side": "LONG", "ts": c[-1][0], "close": c[-1][4],
                   "rsi": 40.0, "tl": 36.0, "cleared_by": 4.0})
    b = PaperBook(); j = Journal(); last = {}
    last = process_asset("BTC/USDT", cs, b, j, last)
    assert b.in_position("BTC/USDT")
    assert last["BTC/USDT"] == cs[-1][0]


def test_idempotent_rerun_books_nothing(monkeypatch):
    import rsi_4h_forward as m
    cs = [candle(i, 101, 99, 100) for i in range(40)]
    monkeypatch.setattr(m, "latest_break", lambda c: None)
    b = PaperBook(); j = Journal()
    last = process_asset("BTC/USDT", cs, b, j, {})
    snap1 = b.snapshot()
    last = process_asset("BTC/USDT", cs, b, j, last)
    assert b.snapshot() == snap1


def test_gap_advances_exits_over_missed_bars(monkeypatch):
    import rsi_4h_forward as m
    monkeypatch.setattr(m, "latest_break", lambda c: None)
    b = PaperBook(); j = Journal()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    cs = [candle(1, 101, 99.5, 100), candle(2, 101, 97.5, 99), candle(3, 100, 99, 99.5)]
    process_asset("BTC/USDT", cs, b, j, {"BTC/USDT": 0})
    assert not b.in_position("BTC/USDT")
    assert j.live_trades == 1


def test_break_while_in_position_logs_skip(monkeypatch):
    import rsi_4h_forward as m
    cs = [candle(i, 101, 99, 100) for i in range(40)]
    monkeypatch.setattr(m, "latest_break",
        lambda c: {"side": "SHORT", "ts": c[-1][0], "close": c[-1][4],
                   "rsi": 60.0, "tl": 64.0, "cleared_by": 4.0})
    b = PaperBook(); j = Journal()
    b.enter("BTC/USDT", "LONG", 100.0, ts=-1)
    process_asset("BTC/USDT", cs, b, j, {"BTC/USDT": -10})
    assert j.skips == 1 and b.in_position("BTC/USDT")


def test_entry_event_includes_tp_sl(monkeypatch, tmp_path):
    import json
    import rsi_4h_forward as m
    cs = [candle(i, 101, 99, 100) for i in range(40)]
    monkeypatch.setattr(m, "latest_break",
        lambda c: {"side": "LONG", "ts": c[-1][0], "close": 100.0,
                   "rsi": 40.0, "tl": 36.0, "cleared_by": 4.0})
    b = PaperBook(); j = Journal(events_path=tmp_path / "e.jsonl")
    process_asset("BTC/USDT", cs, b, j, {})
    evs = [json.loads(l) for l in (tmp_path / "e.jsonl").read_text().splitlines()]
    entry = [e for e in evs if e["event"] == "ENTRY"][0]
    assert abs(entry["tp"] - 103.0) < 1e-9 and abs(entry["sl"] - 98.0) < 1e-9  # +3% / -2%


def test_regime_aligned_long_above_short_below():
    from rsi_4h_forward import _regime_aligned, REGIME_EMA
    up = list(range(1, REGIME_EMA + 50))           # steadily rising → price above EMA200
    assert _regime_aligned("LONG", up) is True      # long with the uptrend → aligned
    assert _regime_aligned("SHORT", up) is False    # short against uptrend → filtered out
    down = list(range(REGIME_EMA + 50, 1, -1))      # steadily falling → price below EMA200
    assert _regime_aligned("SHORT", down) is True
    assert _regime_aligned("LONG", down) is False


def test_regime_aligned_needs_enough_bars():
    from rsi_4h_forward import _regime_aligned
    assert _regime_aligned("LONG", [100, 101, 102]) is False   # too short to judge → not aligned


def test_aligned_break_opens_regime_shadow(monkeypatch):
    import rsi_4h_forward as m
    # rising series so a LONG break is trend-aligned
    cs = [candle(i, lo=100 + i, hi=102 + i, close=101 + i) for i in range(260)]
    monkeypatch.setattr(m, "latest_break",
        lambda c: {"side": "LONG", "ts": c[-1][0], "close": c[-1][4],
                   "rsi": 40.0, "tl": 36.0, "cleared_by": 4.0})
    b = PaperBook(); j = Journal()
    process_asset("BTC/USDT", cs, b, j, {})
    assert len(b.regimes) == 1                      # aligned long → regime shadow opened
