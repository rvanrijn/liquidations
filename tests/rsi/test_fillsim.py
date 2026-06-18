from rsi_paper import RestingOrder, FillSim


def test_buy_fills_at_or_below_limit_at_limit_price():
    fs = FillSim()
    fs.place(RestingOrder(side="BUY", price=100.0, kind="ENTRY", placed_ts=0, expiry_ts=60))
    assert fs.check(101.0, ts=1) == []           # above limit -> no fill
    fills = fs.check(99.5, ts=2)                  # traded through -> fill
    assert len(fills) == 1
    assert fills[0].kind == "ENTRY"
    assert fills[0].price == 100.0               # AT LIMIT, not 99.5


def test_buy_fills_exactly_at_limit():
    fs = FillSim()
    fs.place(RestingOrder(side="BUY", price=100.0, kind="ENTRY", placed_ts=0, expiry_ts=60))
    fills = fs.check(100.0, ts=1)
    assert len(fills) == 1 and fills[0].price == 100.0


def test_entry_expires_after_tif():
    fs = FillSim()
    fs.place(RestingOrder(side="BUY", price=100.0, kind="ENTRY", placed_ts=0, expiry_ts=60))
    expired = fs.expire(now_ts=61)               # past expiry
    assert [o.kind for o in expired] == ["ENTRY"]
    assert fs.check(99.0, ts=62) == []           # gone, no fill


def test_sell_exit_fills_at_or_above_limit_at_limit_price():
    fs = FillSim()
    fs.place(RestingOrder(side="SELL", price=110.0, kind="EXIT", placed_ts=0, expiry_ts=None))
    assert fs.check(109.0, ts=1) == []
    fills = fs.check(110.5, ts=2)
    assert len(fills) == 1 and fills[0].kind == "EXIT" and fills[0].price == 110.0


def test_stop_fills_at_or_below_stop_at_stop_price():
    fs = FillSim()
    fs.place(RestingOrder(side="SELL", price=98.0, kind="STOP", placed_ts=0, expiry_ts=None))
    fills = fs.check(97.5, ts=1)
    assert len(fills) == 1 and fills[0].kind == "STOP" and fills[0].price == 98.0


def test_stop_precedence_over_exit_on_same_print():
    fs = FillSim()
    fs.place(RestingOrder(side="SELL", price=110.0, kind="EXIT", placed_ts=0, expiry_ts=None))
    fs.place(RestingOrder(side="SELL", price=98.0, kind="STOP", placed_ts=0, expiry_ts=None))
    # an impossible-but-defensive print that satisfies both: stop must win, only one fill
    fills = fs.check(97.0, ts=1)
    assert len(fills) == 1 and fills[0].kind == "STOP"


def test_cancel_by_kind():
    fs = FillSim()
    fs.place(RestingOrder(side="SELL", price=110.0, kind="EXIT", placed_ts=0, expiry_ts=None))
    fs.cancel("EXIT")
    assert fs.check(120.0, ts=1) == []
