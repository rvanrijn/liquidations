import pandas as pd
from mcbstrat.signals import build_signals

def _frame(green, red, mf):
    idx = pd.date_range("2024-01-01", periods=len(green), freq="8h", tz="UTC")
    return pd.DataFrame({"green": green, "red": red, "mf": mf}, index=idx)

def test_long_entry_then_exit_on_mf_red():
    df = _frame(
        green=[True, False, False, False, False],
        red=  [False, False, False, False, False],
        mf=   [ 10.0,  5.0,   2.0,  -1.0,  -3.0],
    )
    sig = build_signals(df)
    assert list(sig["position"]) == [1, 1, 1, 0, 0]
    assert list(sig["event"]) == ["ENTER_LONG", "", "", "EXIT_LONG", ""]

def test_long_exit_does_not_auto_open_short():
    df = _frame(green=[True, False], red=[False, False], mf=[10.0, -5.0])
    sig = build_signals(df)
    assert list(sig["position"]) == [1, 0]

def test_short_entry_requires_red_dot_and_mf_red():
    df = _frame(green=[False, False, False], red=[True, False, False], mf=[-5.0, -2.0, 3.0])
    sig = build_signals(df)
    assert list(sig["position"]) == [-1, -1, 0]
    assert list(sig["event"]) == ["ENTER_SHORT", "", "EXIT_SHORT"]

def test_no_entry_when_dot_and_mf_disagree():
    df = _frame(green=[True, True], red=[False, False], mf=[-5.0, -1.0])
    sig = build_signals(df)
    assert list(sig["position"]) == [0, 0]


def test_arm_window_zero_requires_same_bar_cooccurrence():
    # green dot bar has mf<0; mf turns green only on the NEXT bar -> strict (W=0) takes nothing
    df = _frame(green=[True, False], red=[False, False], mf=[-5.0, 3.0])
    sig = build_signals(df, arm_window=0)
    assert list(sig["position"]) == [0, 0]

def test_armed_window_allows_delayed_long_entry():
    # green dot at bar0 (mf red), mf confirms green at bar2; arm_window=2 covers it
    df = _frame(green=[True, False, False, False], red=[False]*4, mf=[-5.0, -2.0, 3.0, 4.0])
    sig = build_signals(df, arm_window=2)
    assert list(sig["position"]) == [0, 0, 1, 1]
    assert sig["event"].iloc[2] == "ENTER_LONG"

def test_arm_expires_after_window():
    # same setup but arm_window=1 -> arm dies before the bar2 confirmation, no entry
    df = _frame(green=[True, False, False, False], red=[False]*4, mf=[-5.0, -2.0, 3.0, 4.0])
    sig = build_signals(df, arm_window=1)
    assert list(sig["position"]) == [0, 0, 0, 0]

def test_armed_window_delayed_short_entry():
    # red dot at bar0 (mf green), mf confirms red at bar2; arm_window=3 covers it
    df = _frame(green=[False]*4, red=[True, False, False, False], mf=[5.0, 2.0, -3.0, -4.0])
    sig = build_signals(df, arm_window=3)
    assert list(sig["position"]) == [0, 0, -1, -1]
    assert sig["event"].iloc[2] == "ENTER_SHORT"
