"""End-to-end: data -> HA -> indicators -> signals -> backtest -> metrics -> report."""
from pathlib import Path
import pandas as pd
from mcbstrat.data import load_btc_8h
from mcbstrat.heikin_ashi import add_heikin_ashi
from mcbstrat.indicators import ha_hlc3, wavetrend, money_flow, detect_dots
from mcbstrat.signals import build_signals
from mcbstrat.backtest import run_backtest, CostModel
from mcbstrat.metrics import summarize, verdict_for_side, split_oos, tag_regime
from mcbstrat.report import render_markdown

REPORTS = Path(__file__).resolve().parent.parent / "reports"

def _enrich(df):
    df = add_heikin_ashi(df)
    src = ha_hlc3(df)
    wt1, wt2 = wavetrend(src)
    df["mf"] = money_flow(df)
    df["green"], df["red"] = detect_dots(wt1, wt2)
    return build_signals(df[["open", "high", "low", "close", "green", "red", "mf"]])

def main():
    REPORTS.mkdir(exist_ok=True)
    raw = load_btc_8h(years=3.0)
    sig = _enrich(raw)
    cost = CostModel(fee_side_pct=0.05, slippage_pct=0.05)
    trades, _ = run_backtest(sig, cost)
    trades0, _ = run_backtest(sig, CostModel(0.0, 0.0))

    longs = [t for t in trades if t.side == "LONG"]
    shorts = [t for t in trades if t.side == "SHORT"]
    ins, out = split_oos(trades)
    windows = [
        ("BULL_23_24",  pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2024-03-31", tz="UTC")),
        ("CHOP_BEAR_24", pd.Timestamp("2024-04-01", tz="UTC"), pd.Timestamp("2024-10-31", tz="UTC")),
        ("BULL2_25_26", pd.Timestamp("2024-11-01", tz="UTC"), pd.Timestamp("2030-01-01", tz="UTC")),
    ]
    regimes = {}
    for name, lo, hi in windows:
        rt = [t for t in trades if tag_regime(t.entry_time, windows) == name]
        regimes[name] = summarize(rt)

    payload = {
        "symbol": "BTCUSDT", "bars": len(raw),
        "span": f"{raw.index[0].date()} .. {raw.index[-1].date()}",
        "cost": {"all": summarize(trades), "long": summarize(longs), "short": summarize(shorts)},
        "zero_cost": {"all": summarize(trades0)},
        "verdict_long": verdict_for_side(longs),
        "verdict_short": verdict_for_side(shorts),
        "oos": {"in": summarize(ins), "out": summarize(out)},
        "regimes": regimes,
        "tv_crosscheck": "see tv_crosscheck.py output (manual)",
    }
    md = render_markdown(payload)
    (REPORTS / "mcb_cipherb_8h_backtest.md").write_text(md)
    print(md)

if __name__ == "__main__":
    main()
