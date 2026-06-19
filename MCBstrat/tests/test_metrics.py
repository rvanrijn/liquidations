import pandas as pd
from mcbstrat.backtest import Trade
from mcbstrat.metrics import summarize, verdict_for_side, MIN_TRADES, tag_regime

def _t(side, ret, t0, t1):
    return Trade(side, pd.Timestamp(t0, tz="UTC"), pd.Timestamp(t1, tz="UTC"), 100, 100*(1+ret), ret, 0.0, 0.0, 1)

def test_summarize_basic_stats():
    trades = [_t("LONG", 0.10, "2024-01-01", "2024-01-02"),
              _t("LONG", -0.05, "2024-02-01", "2024-02-02"),
              _t("LONG", 0.20, "2024-03-01", "2024-03-02")]
    s = summarize(trades)
    assert s["n"] == 3
    assert s["wins"] == 2
    assert round(s["win_rate"], 4) == round(2/3, 4)
    assert s["profit_factor"] > 1
    assert round(s["expectancy"], 4) == round((0.10 - 0.05 + 0.20) / 3, 4)

def test_verdict_insufficient_power_below_min_trades():
    assert verdict_for_side([_t("SHORT", 0.01, "2024-01-01", "2024-01-02")]) == "inconclusive / insufficient power"

def test_verdict_uses_expectancy_when_enough_trades():
    losers = [_t("SHORT", -0.02, f"2024-{m:02d}-01", f"2024-{m:02d}-02") for m in range(1, 13)] * 2
    assert len(losers) >= MIN_TRADES
    assert verdict_for_side(losers) == "negative edge"

def test_tag_regime_halfopen_contiguous_windows_have_no_gap():
    w = [
        ("A", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-04-01", tz="UTC")),
        ("B", pd.Timestamp("2024-04-01", tz="UTC"), pd.Timestamp("2024-11-01", tz="UTC")),
    ]
    # a non-midnight 8H bar on the last day of window A must still tag A (not UNTAGGED)
    assert tag_regime(pd.Timestamp("2024-03-31 16:00", tz="UTC"), w) == "A"
    # the boundary instant belongs to the NEXT window (half-open: hi is exclusive)
    assert tag_regime(pd.Timestamp("2024-04-01 00:00", tz="UTC"), w) == "B"
    # last day of B at a non-midnight bar still tags B
    assert tag_regime(pd.Timestamp("2024-10-31 16:00", tz="UTC"), w) == "B"
