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
