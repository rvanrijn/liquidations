"""Sweep the entry arming window: does relaxing the dot+MF co-occurrence recover an edge?

W = number of bars AFTER the dot during which money-flow may still confirm the entry.
W=0 is the original strict same-bar rule; larger W decouples the dot and the MF confirm.
"""
import pandas as pd
from mcbstrat.data import load_btc_8h
from mcbstrat.heikin_ashi import add_heikin_ashi
from mcbstrat.indicators import ha_hlc3, wavetrend, money_flow, detect_dots
from mcbstrat.signals import build_signals
from mcbstrat.backtest import run_backtest, CostModel
from mcbstrat.metrics import summarize

WINDOWS = [0, 1, 2, 3, 5, 8, 13]

def _features(df):
    df = add_heikin_ashi(df)
    wt1, wt2 = wavetrend(ha_hlc3(df))
    df["mf"] = money_flow(df)
    df["green"], df["red"] = detect_dots(wt1, wt2)
    return df[["open", "high", "low", "close", "green", "red", "mf"]]

def main():
    feats = _features(load_btc_8h(years=3.0))
    cost = CostModel(fee_side_pct=0.05, slippage_pct=0.05)
    hdr = f"{'W':>3} {'trades':>6} {'long':>4} {'short':>5} {'win%':>6} {'PF':>5} {'exp%':>7} {'net%':>8} {'zero%':>8} {'maxDD%':>7}"
    print(f"# MCB 8H HA — entry arming-window sweep ({feats.index[0].date()}..{feats.index[-1].date()}, {len(feats)} bars)")
    print(hdr)
    print("-" * len(hdr))
    for w in WINDOWS:
        sig = build_signals(feats, arm_window=w)
        tr, _ = run_backtest(sig, cost)
        tr0, _ = run_backtest(sig, CostModel(0.0, 0.0))
        s, s0 = summarize(tr), summarize(tr0)
        longs = sum(1 for t in tr if t.side == "LONG")
        shorts = sum(1 for t in tr if t.side == "SHORT")
        print(f"{w:>3} {s['n']:>6} {longs:>4} {shorts:>5} {s['win_rate']*100:>5.1f} "
              f"{s['profit_factor']:>5.2f} {s['expectancy']*100:>6.2f} {s['total_return']*100:>7.1f} "
              f"{s0['total_return']*100:>7.1f} {s['max_dd']*100:>6.1f}")

if __name__ == "__main__":
    main()
