"""Tests for PaperBook — the core bracket-exit + hold-48 shadow engine.

Run:  python -m pytest tests/rsi/test_4h_book.py -v
"""
import json
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../RSI"))

from rsi_4h_forward import PaperBook


def bar(ts, hi, lo, close):
    return {"ts": ts, "high": hi, "low": lo, "close": close}


def test_enter_opens_one_position_and_sets_levels():
    b = PaperBook()
    p = b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    assert b.in_position("BTC/USDT")
    assert abs(p.stop_price - 98.0) < 1e-9 and abs(p.tp_price - 103.0) < 1e-9


def test_second_break_while_in_position_is_skipped():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    assert b.enter("BTC/USDT", "SHORT", 101.0, ts=1) is None
    assert b.skips == 1


def test_long_take_profit_books_win():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    out = b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.2))
    live = [t for t in out if t.kind == "live"][0]
    assert live.reason == "TP" and live.exit_price == 103.0 and live.pnl > 0
    assert not b.in_position("BTC/USDT")


def test_long_stop_before_tp_when_both_touch():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    out = b.advance("BTC/USDT", bar(1, hi=104.0, lo=97.0, close=101.0))
    live = [t for t in out if t.kind == "live"][0]
    assert live.reason == "STOP" and live.exit_price == 98.0 and live.pnl < 0


def test_short_take_profit():
    b = PaperBook()
    b.enter("ETH/USDT", "SHORT", 100.0, ts=0)
    out = b.advance("ETH/USDT", bar(1, hi=100.5, lo=96.5, close=97.0))
    live = [t for t in out if t.kind == "live"][0]
    assert live.reason == "TP" and abs(live.exit_price - 97.0) < 1e-9 and live.pnl > 0


def test_cap_closes_after_48_bars_at_close():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    out = []
    for k in range(1, 49):
        out += b.advance("BTC/USDT", bar(k, hi=101.0, lo=99.5, close=100.5))
    live = [t for t in out if t.kind == "live"]
    assert len(live) == 1 and live[0].reason == "CAP" and live[0].exit_price == 100.5


def test_shadow_resolves_independently_of_live():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.0))
    assert not b.in_position("BTC/USDT")
    assert len(b.shadow_trades) == 0
    for k in range(2, 50):
        b.advance("BTC/USDT", bar(k, hi=104.0, lo=99.0, close=103.5))
    assert len(b.shadow_trades) == 1 and b.shadow_trades[0].reason == "HOLD"


def test_fee_applied_both_legs():
    b = PaperBook(fee=0.0002)
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    out = b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.2))
    live = [t for t in out if t.kind == "live"][0]
    assert abs(live.ret - ((1.03) * (1 - 0.0002) ** 2 - 1)) < 1e-9


def test_snapshot_restore_roundtrip():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=5)
    snap = b.snapshot()
    b2 = PaperBook()
    b2.restore(snap)
    assert b2.in_position("BTC/USDT")
    assert b2.positions["BTC/USDT"].entry_price == 100.0
    assert len(b2.shadows) == 1


def test_bars_survive_json_restart_and_cap_fires_at_right_count():
    # The load-bearing cron property: bars must survive a JSON state round-trip so
    # the 8-day cap counts from the correct absolute bar after a process restart.
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    for k in range(1, 11):                      # 10 bars, no stop/TP
        b.advance("BTC/USDT", bar(k, hi=101.0, lo=99.5, close=100.5))
    assert b.positions["BTC/USDT"].bars == 10
    snap = json.loads(json.dumps(b.snapshot()))  # exactly what save_state/load_state does
    b2 = PaperBook(); b2.restore(snap)
    assert b2.positions["BTC/USDT"].bars == 10
    out = []
    for k in range(11, 49):                     # advance to absolute bar 48
        out += b2.advance("BTC/USDT", bar(k, hi=101.0, lo=99.5, close=100.5))
    live = [t for t in out if t.kind == "live"]
    assert len(live) == 1 and live[0].reason == "CAP"   # CAP at bar 48, not mis-counted


def test_short_stop_before_tp_when_both_touch():
    b = PaperBook()
    b.enter("ETH/USDT", "SHORT", 100.0, ts=0)   # stop 102, tp 97
    out = b.advance("ETH/USDT", bar(1, hi=103.0, lo=96.0, close=99.0))  # both touched
    live = [t for t in out if t.kind == "live"][0]
    assert live.reason == "STOP" and abs(live.exit_price - 102.0) < 1e-9 and live.pnl < 0


def test_two_assets_advance_independently():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    b.enter("ETH/USDT", "SHORT", 200.0, ts=0)
    # one bar that TPs BTC (>=103) but does nothing to ETH
    out = b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.2))
    assert any(t.kind == "live" and t.reason == "TP" for t in out)
    assert not b.in_position("BTC/USDT") and b.in_position("ETH/USDT")


def test_ladder_half_tp_then_remainder_stops_small_win():
    b = PaperBook()  # ladder: 0.5 @ +3%, rest runs with -2% stop
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.0))   # half banked at 103
    out = b.advance("BTC/USDT", bar(2, hi=101.0, lo=97.5, close=99.0))  # remainder stops at 98
    lad = [t for t in out if t.kind == "ladder"][0]
    # acc = 0.5*1.03 + 0.5*0.98 = 1.005 ; net = (1-fee)^2*1.005 - 1 > 0
    assert lad.reason == "STOP" and lad.pnl > 0
    assert abs(lad.ret - ((1 - 0.0002) ** 2 * 1.005 - 1)) < 1e-9


def test_ladder_runner_captures_trend_to_cap():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.0))   # half off at +3%
    out = []
    for k in range(2, 49):                                          # runner to 48-bar cap, close 108
        out += b.advance("BTC/USDT", bar(k, hi=109.0, lo=99.0, close=108.0))
    lad = [t for t in out if t.kind == "ladder"][0]
    # acc = 0.5*1.03 + 0.5*1.08 = 1.055
    assert lad.reason == "CAP" and abs(lad.ret - ((1 - 0.0002) ** 2 * 1.055 - 1)) < 1e-9
    assert lad.pnl > b.live_trades[0].pnl   # ladder beats the live +3% TP that took the whole position


def test_ladder_stop_before_partial_full_loss():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    out = b.advance("BTC/USDT", bar(1, hi=104.0, lo=97.0, close=101.0))  # both touch; stop wins on full
    lad = [t for t in out if t.kind == "ladder"][0]
    assert lad.reason == "STOP" and lad.pnl < 0
    assert abs(lad.ret - ((1 - 0.0002) ** 2 * 0.98 - 1)) < 1e-9   # whole position at -2%


def test_ladder_survives_snapshot_restore():
    import json
    b = PaperBook(); b.enter("ETH/USDT", "SHORT", 200.0, ts=5)
    b.advance("ETH/USDT", bar(1, hi=200.5, lo=193.5, close=194.0))   # SHORT half off at +3% (194)
    snap = json.loads(json.dumps(b.snapshot()))
    b2 = PaperBook(); b2.restore(snap)
    assert len(b2.ladders) == 1 and b2.ladders[0].partial_done and abs(b2.ladders[0].remaining - 0.5) < 1e-9


def test_regime_shadow_only_opens_when_aligned():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0, regime_ok=False)   # not trend-aligned
    assert len(b.regimes) == 0                                   # no regime shadow
    b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.2))  # closes live
    b.enter("BTC/USDT", "LONG", 100.0, ts=2, regime_ok=True)    # aligned
    assert len(b.regimes) == 1


def test_regime_shadow_uses_live_bracket_and_books_regime_kind():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0, regime_ok=True)
    out = b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.2))  # TP at +3%
    reg = [t for t in out if t.kind == "regime"]
    assert len(reg) == 1 and reg[0].reason == "TP" and reg[0].exit_price == 103.0
    assert len(b.regime_trades) == 1 and not b.regimes        # closed, list emptied


def test_regime_shadow_survives_snapshot_restore():
    import json
    b = PaperBook(); b.enter("BTC/USDT", "LONG", 100.0, ts=5, regime_ok=True)
    snap = json.loads(json.dumps(b.snapshot()))
    b2 = PaperBook(); b2.restore(snap)
    assert len(b2.regimes) == 1 and b2.regimes[0].entry_price == 100.0
