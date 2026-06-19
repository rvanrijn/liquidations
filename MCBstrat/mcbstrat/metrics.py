"""Aggregate stats, OOS split, regime tagging, minimum-N verdict gate."""
from __future__ import annotations
import pandas as pd

MIN_TRADES = 20

def summarize(trades) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "expectancy": 0.0, "total_return": 0.0, "max_dd": 0.0, "avg_bars": 0.0}
    rets = [t.return_pct for t in trades]
    wins = sum(1 for r in rets if r > 0)
    gross_win = sum(r for r in rets if r > 0)
    gross_loss = -sum(r for r in rets if r < 0)
    eq = 1.0
    curve = []
    for r in rets:
        eq *= (1 + r)
        curve.append(eq)
    peak, max_dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)
    return {
        "n": n, "wins": wins, "win_rate": wins / n,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy": sum(rets) / n,
        "total_return": curve[-1] - 1,
        "max_dd": max_dd,
        "avg_bars": sum(t.bars_held for t in trades) / n,
    }

def verdict_for_side(trades) -> str:
    if len(trades) < MIN_TRADES:
        return "inconclusive / insufficient power"
    exp = sum(t.return_pct for t in trades) / len(trades)
    if exp > 0.002:
        return "positive edge"
    if exp < 0:
        return "negative edge"
    return "marginal"

def split_oos(trades, frac_in: float = 0.70):
    """Chronological split by trade order. Returns (in_sample, out_of_sample)."""
    trades = sorted(trades, key=lambda t: t.entry_time)
    k = int(len(trades) * frac_in)
    return trades[:k], trades[k:]

def tag_regime(ts: pd.Timestamp, windows: list[tuple[str, pd.Timestamp, pd.Timestamp]]) -> str:
    for name, lo, hi in windows:
        if lo <= ts < hi:
            return name
    return "UNTAGGED"
