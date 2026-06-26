"""Tests for PaperBook — the core bracket-exit + hold-48 shadow engine.

Run:  python -m pytest tests/rsi/test_4h_book.py -v
"""
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
