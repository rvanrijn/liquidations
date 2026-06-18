import numpy as np
from rsi_paper import Strategy, Indicators, FillSim, Journal


def _series_to(target_rsi_low=True, n=1000, base=100.0):
    """Build closes whose final RSI is low (downtrend tail) or high (uptrend tail)."""
    closes = [base + 10 * np.sin(i / 7.0) for i in range(n - 30)]
    tail = ([closes[-1] - i for i in range(1, 31)] if target_rsi_low
            else [closes[-1] + i for i in range(1, 31)])
    return closes + tail


def _mk(closes):
    ind = Indicators(); ind.seed(closes)
    fs = FillSim(); j = Journal(events_path=None, state_path=None)
    s = Strategy(ind, fs, j)
    return s, ind, fs, j


def test_arms_entry_when_rsi_low_and_above_ema():
    closes = _series_to(target_rsi_low=True)
    # force last close above EMA by lifting whole series baseline is hard;
    # instead assert: if rsi<30 and close>ema, an ENTRY order is armed.
    s, ind, fs, j = _mk(closes)
    s.on_bar_close({"close": closes[-1], "ts": 60_000})
    if ind.rsi < 30 and closes[-1] > ind.ema:
        assert fs.has("ENTRY")
        assert j.entry_arms == 1
    else:
        assert not fs.has("ENTRY")  # regime/threshold not met -> no arm


def test_entry_fill_transitions_to_long_and_sets_stop():
    s, ind, fs, j = _mk(_series_to(True))
    # Manually arm to make the test deterministic regardless of EMA position:
    s._force_arm(price=100.0, ts=0)
    fills = s.on_trade(price=99.0, ts=30_000)  # fill at 100.0
    assert s.state == "LONG"
    assert s.position.entry_price == 100.0
    assert abs(s.position.stop_price - 98.0) < 1e-9  # 2% stop
    assert fs.has("STOP")


def test_entry_miss_after_tif_returns_flat():
    s, ind, fs, j = _mk(_series_to(True))
    s._force_arm(price=100.0, ts=0)
    s.on_trade(price=101.0, ts=30_000)          # above limit, no fill
    s.on_bar_close({"close": 101.0, "ts": 61_000})  # past 60s TIF -> expire
    assert s.state == "FLAT"
    assert not fs.has("ENTRY")


def test_tp_path_books_maker_fill_at_limit():
    s, ind, fs, j = _mk(_series_to(True))
    s._force_arm(price=100.0, ts=0)
    s.on_trade(price=100.0, ts=10_000)          # enter long @100
    s._force_exit_arm(price=110.0, ts=20_000)   # RSI>=55 exit limit @110
    s.on_trade(price=111.0, ts=30_000)          # TP fill @110
    assert s.state == "FLAT"
    assert j.exit_fills == 1 and j.trades == 1
    # gross +10% on 5000 notional, plus maker rebate both legs (>0)
    assert s.balance > 5000.0


def test_stop_path_books_taker_and_missed_exit_flag():
    s, ind, fs, j = _mk(_series_to(True))
    s._force_arm(price=100.0, ts=0)
    s.on_trade(price=100.0, ts=10_000)          # long @100, stop @98
    s._force_exit_arm(price=110.0, ts=20_000)   # exit armed but never fills
    s.on_trade(price=97.0, ts=30_000)           # stop fill @98
    assert s.state == "FLAT"
    assert j.missed_exit_to_stop == 1           # exit was armed, stop hit instead
    assert s.balance < 5000.0


def test_gap_while_armed_marks_indeterminate():
    s, ind, fs, j = _mk(_series_to(True))
    s._force_arm(price=100.0, ts=0)
    s.on_gap(now_ts=120_000)
    assert s.state == "FLAT"
    assert not fs.has("ENTRY")
    # arm that was interrupted is NOT counted as a miss
    assert j.entry_arms == 1 and j.entry_fills == 0


def test_exit_reprice_counts_one_arm_opportunity():
    s, ind, fs, j = _mk(_series_to(True))
    s._force_arm(price=100.0, ts=0)
    s.on_trade(price=100.0, ts=10_000)            # LONG @100
    s._force_exit_arm(price=110.0, ts=20_000)     # first arm
    s._force_exit_arm(price=109.0, ts=80_000)     # re-price (chase) — must NOT recount
    s._force_exit_arm(price=108.0, ts=140_000)    # re-price again
    assert j.exit_arms == 1                        # one opportunity, not three
    assert fs.has("EXIT")                          # the latest exit limit is resting
    s.on_trade(price=108.5, ts=150_000)           # fills the @108 limit
    assert j.exit_fills == 1 and j.exit_fill_rate() == 1.0
