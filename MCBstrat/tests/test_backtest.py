import pandas as pd
from mcbstrat.backtest import run_backtest, CostModel

def _bars(opens, closes, events):
    n = len(opens)
    idx = pd.date_range("2024-01-01", periods=n, freq="8h", tz="UTC")
    return pd.DataFrame({
        "open": opens,
        "high": [max(o, c) + 1 for o, c in zip(opens, closes)],
        "low":  [min(o, c) - 1 for o, c in zip(opens, closes)],
        "close": closes,
        "position": [ {"ENTER_LONG":1,"EXIT_LONG":0,"ENTER_SHORT":-1,"EXIT_SHORT":0}.get(e, None) for e in events ],
        "event": events,
    }, index=idx)

def test_long_trade_fills_at_next_real_open_with_costs():
    df = _bars(
        opens=[ 50, 100, 105, 110],
        closes=[60,  101, 106, 111],
        events=["ENTER_LONG", "", "EXIT_LONG", ""],
    )
    trades, _ = run_backtest(df, CostModel(fee_side_pct=0.05, slippage_pct=0.05))
    assert len(trades) == 1
    t = trades[0]
    assert t.side == "LONG"
    assert round(t.entry_price, 4) == round(100 * (1 + 0.0005), 4)
    assert round(t.exit_price, 4) == round(110 * (1 - 0.0005), 4)
    assert 0.094 < t.return_pct < 0.099

def test_no_lookahead_entry_and_exit_use_next_bar_open():
    # 4 bars so the EXIT signal (bar2) has a real next bar (bar3) to fill at.
    df = _bars(opens=[50, 100, 110, 120], closes=[60, 101, 111, 121],
               events=["ENTER_LONG", "", "EXIT_LONG", ""])
    trades, _ = run_backtest(df, CostModel(0.0, 0.0))
    assert len(trades) == 1
    assert trades[0].entry_price == 100.0   # bar1 open (next after ENTER@bar0); NOT bar0 open(50)/close(60)
    assert trades[0].exit_price == 120.0     # bar3 open (next after EXIT@bar2); NOT bar2 open(110)/close(111)

def test_open_position_closed_at_final_bar_close():
    df = _bars(opens=[50, 100, 105], closes=[60, 101, 130], events=["ENTER_LONG", "", ""])
    trades, _ = run_backtest(df, CostModel(0.0, 0.0))
    assert len(trades) == 1
    assert trades[0].exit_price == 130.0
