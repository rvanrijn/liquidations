#!/usr/bin/env python3
"""
Compare the ORIGINAL RSI strategy across fast timeframes.

Strategy (unchanged from rsi_backtest.py defaults):
  long when RSI(14) < 30, exit when RSI >= 50, 2% stop loss, $5000, long-only.

Binance only serves native 1m and 1s klines, so 5s/10s/30s are RESAMPLED
from a single 1s fetch. 6 months is infeasible at 1s resolution
(~15M bars) -- use a short recent window (default 2 days).

Usage:
  python rsi_tf_compare.py --symbol BTC/USDT --days 2
"""

import argparse
from datetime import datetime, timezone, timedelta

import ccxt
import numpy as np

from rsi_backtest import calculate_rsi, backtest  # same dir


def fetch_1s(symbol, since_ms):
    ex = ccxt.binance({"enableRateLimit": True})
    out, cursor = [], since_ms
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe="1s", since=cursor, limit=1000)
        if not batch:
            break
        out.extend(batch)
        cursor = batch[-1][0] + 1
        if len(batch) < 1000:
            break
    seen, uniq = set(), []
    for c in out:
        if c[0] not in seen:
            seen.add(c[0])
            uniq.append(c)
    return uniq


def resample(base_1s, sec):
    """Aggregate 1s OHLCV into `sec`-second buckets aligned to epoch."""
    if sec == 1:
        return base_1s
    bucket_ms = sec * 1000
    out = []
    cur_key = None
    o = h = l = c = v = None
    for ts, op, hi, lo, cl, vol in base_1s:
        key = ts - (ts % bucket_ms)
        if key != cur_key:
            if cur_key is not None:
                out.append([cur_key, o, h, l, c, v])
            cur_key, o, h, l, c, v = key, op, hi, lo, cl, vol
        else:
            h = max(h, hi)
            l = min(l, lo)
            c = cl
            v += vol
    if cur_key is not None:
        out.append([cur_key, o, h, l, c, v])
    return out


def run_one(candles, exit_rsi, args):
    trades, equity, _, _ = backtest(
        candles, capital=args.capital, stop_pct=args.stop_pct, fee=args.fee,
        side="long", entry_rsi=30.0, exit_rsi=exit_rsi,
    )
    net = (equity / args.capital - 1) * 100
    win = (sum(t["pnl"] > 0 for t in trades) / len(trades) * 100) if trades else 0.0
    return net, len(trades), win


def run_sweep(base, tfs, args):
    exits = list(range(35, 76, 5))
    resampled = {name: resample(base, sec) for name, sec in tfs}
    print(f"Exit-RSI sweep | entry RSI<30, {args.stop_pct*100:.0f}% SL, "
          f"fee={args.fee*100:.3f}%/side. Cells = net% (trades)\n")
    head = "exit │ " + " ".join(f"{n:>14}" for n, _ in tfs)
    print(head)
    print("─" * len(head))
    best = {n: (-1e9, None) for n, _ in tfs}
    for ex in exits:
        cells = []
        for name, _ in tfs:
            net, ntr, _ = run_one(resampled[name], float(ex), args)
            cells.append(f"{net:>+7.2f}% ({ntr:>3})")
            if net > best[name][0]:
                best[name] = (net, ex)
        print(f"{ex:>4} │ " + " ".join(f"{c:>14}" for c in cells))
    print("\nBest exit RSI per timeframe (max net%):")
    for name, _ in tfs:
        net, ex = best[name]
        print(f"  {name:>4}: exit RSI {ex}  ->  {net:+.2f}%")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--days", type=float, default=2.0)
    p.add_argument("--capital", type=float, default=5000.0)
    p.add_argument("--stop-pct", type=float, default=0.02)
    p.add_argument("--fee", type=float, default=0.0004, help="per-side fee fraction")
    p.add_argument("--sweep-exit", action="store_true",
                   help="grid-search exit RSI per timeframe")
    args = p.parse_args()

    since = datetime.now(tz=timezone.utc) - timedelta(days=args.days)
    since_ms = int(since.timestamp() * 1000)

    print(f"Fetching {args.symbol} 1s since {since.strftime('%Y-%m-%d %H:%M')} UTC ...")
    base = fetch_1s(args.symbol, since_ms)
    span_h = (base[-1][0] - base[0][0]) / 3.6e6
    print(f"Got {len(base):,} 1s candles over {span_h:.1f}h\n")

    tfs = [("5s", 5), ("10s", 10), ("30s", 30), ("1m", 60)]

    if args.sweep_exit:
        run_sweep(base, tfs, args)
        return

    print(f"Strategy: LONG RSI<30, exit RSI>=50, {args.stop_pct*100:.0f}% SL, "
          f"${args.capital:,.0f}, fee={args.fee*100:.3f}%/side\n")
    hdr = (f"{'TF':>4} {'bars':>8} {'trades':>7} {'win%':>6} {'avgW%':>7} "
           f"{'avgL%':>7} {'net%':>8} {'end$':>11} {'maxDD%':>7}")
    print(hdr)
    print("-" * len(hdr))

    for name, sec in tfs:
        candles = resample(base, sec)
        trades, equity, open_pos, _ = backtest(
            candles, capital=args.capital, stop_pct=args.stop_pct, fee=args.fee,
            side="long", entry_rsi=30.0, exit_rsi=50.0,
        )
        if not trades:
            print(f"{name:>4} {len(candles):>8,} {0:>7}   (no trades)")
            continue
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        curve = np.array([args.capital] + [t["equity"] for t in trades])
        peak = np.maximum.accumulate(curve)
        max_dd = ((curve - peak) / peak).min() * 100
        avg_w = np.mean([t["ret_pct"] for t in wins]) if wins else 0.0
        avg_l = np.mean([t["ret_pct"] for t in losses]) if losses else 0.0
        net = (equity / args.capital - 1) * 100
        print(f"{name:>4} {len(candles):>8,} {len(trades):>7} "
              f"{len(wins)/len(trades)*100:>5.1f}% {avg_w:>7.2f} {avg_l:>7.2f} "
              f"{net:>+7.2f}% {equity:>10,.0f} {max_dd:>6.1f}%")

    bh = (base[-1][4] / base[0][4] - 1) * 100
    print(f"\nBuy & hold over window: {bh:+.2f}%")


if __name__ == "__main__":
    main()
