#!/usr/bin/env python3
"""
Two checks on the 1m RSI(30 -> exit) mean-reversion signal.

PART A - Robustness: run exit=55 across four non-overlapping 90-day windows
         for BTC and ETH, reporting the GROSS (zero-fee) edge and the net at
         a -0.005%/side maker rebate. Question: is the ~+10%/90d edge stable
         or is it specific to one quarter / one coin?

PART B - Realistic maker fills: a passive limit buy at the signal close only
         fills if a LATER bar trades down to it (low <= limit) within a fill
         window; otherwise the trade is MISSED. This captures adverse
         selection (you miss the immediate bounces, you catch the continuations
         into your stop). Entry + TP exit pay the maker fee; stop pays taker.
"""

import json
import os
from datetime import datetime, timezone, timedelta

import numpy as np

from rsi_backtest import calculate_rsi, fetch_ohlcv, backtest, ema

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data")


def cached_fetch(sym, since_ms, days):
    """Fetch 1m and cache per (symbol, days, UTC-date) so re-runs are instant."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    path = os.path.join(CACHE_DIR, f"cache_{sym.replace('/', '')}_{days}d_{stamp}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    data = fetch_ohlcv(sym, "1m", since_ms)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def stats(trades, equity, cap):
    net = (equity / cap - 1) * 100
    win = (sum(t["pnl"] > 0 for t in trades) / len(trades) * 100) if trades else 0.0
    return net, len(trades), win


def split_windows(candles, n_windows, wdays):
    """Slice a contiguous 1m series into n non-overlapping wdays-day windows."""
    wms = wdays * 86400 * 1000
    t0 = candles[0][0]
    out = []
    for k in range(n_windows):
        lo, hi = t0 + k * wms, t0 + (k + 1) * wms
        out.append([c for c in candles if lo <= c[0] < hi])
    return out


def backtest_maker(candles, capital=5000.0, stop_pct=0.02,
                   entry_rsi=30.0, exit_rsi=55.0,
                   maker_fee=-0.00005, taker_fee=0.0002, fill_window=1,
                   regime_ema=0):
    """Long-only with realistic passive-limit entry fills.

    On RSI<entry at bar i: rest a limit buy at close[i]. It fills (as maker)
    only if some bar k in (i, i+fill_window] has low[k] <= close[i]; entry is
    booked at that limit price from bar k. If never touched -> trade missed.
    Exit: 2% stop (taker) or RSI>=exit on close (maker).
    regime_ema>0: only take signals when close > EMA(regime_ema)."""
    ts = np.array([c[0] for c in candles], dtype=np.int64)
    high = np.array([c[2] for c in candles], dtype=float)
    low = np.array([c[3] for c in candles], dtype=float)
    close = np.array([c[4] for c in candles], dtype=float)
    rsi = calculate_rsi(close)
    trend = ema(close, regime_ema) if regime_ema else None
    n = len(close)

    equity = capital
    trades = []
    signals = fills = misses = 0
    j = 0
    while j < n:
        if np.isnan(rsi[j]) or rsi[j] >= entry_rsi:
            j += 1
            continue
        if regime_ema and (trend is None or np.isnan(trend[j]) or close[j] <= trend[j]):
            j += 1
            continue
        signals += 1
        limit = close[j]
        entry_i = None
        for k in range(j + 1, min(j + 1 + fill_window, n)):
            if low[k] <= limit:
                entry_i = k
                break
        if entry_i is None:
            misses += 1
            j += 1
            continue
        fills += 1
        entry_px = limit
        stop_px = entry_px * (1 - stop_pct)
        equity *= (1 - maker_fee)          # entry leg (rebate if negative)
        exit_i = None
        for m in range(entry_i, n):
            if low[m] <= stop_px:
                ret = stop_px / entry_px - 1
                equity *= (1 + ret) * (1 - taker_fee)   # stop = taker
                reason, exit_i = "STOP", m
                break
            if rsi[m] >= exit_rsi:
                ret = close[m] / entry_px - 1
                equity *= (1 + ret) * (1 - maker_fee)   # TP = maker
                reason, exit_i = "TP", m
                break
        if exit_i is None:
            break
        trades.append({"ret_pct": ret * 100, "pnl": ret, "reason": reason})
        j = exit_i + 1
    return trades, equity, signals, fills, misses


def part_a():
    print("=" * 64)
    print("PART A - Robustness: exit RSI 55, 4 x 90-day windows")
    print("=" * 64)
    n_win, wdays, cap = 4, 90, 5000.0
    total_days = n_win * wdays + 2
    since = datetime.now(tz=timezone.utc) - timedelta(days=total_days)
    since_ms = int(since.timestamp() * 1000)
    data = {}
    for sym in ("BTC/USDT", "ETH/USDT"):
        print(f"\n{sym}")
        c = cached_fetch(sym, since_ms, total_days)
        data[sym] = c
        wins = split_windows(c, n_win, wdays)
        print(f"  {'window (start UTC)':>20} {'trades':>7} {'win%':>6} "
              f"{'gross%':>8} {'maker-0.005%':>12} {'B&H%':>7}")
        g_list, m_list = [], []
        for w in wins:
            if len(w) < 1000:
                continue
            start = datetime.fromtimestamp(w[0][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            tr0, eq0, _, _ = backtest(w, capital=cap, stop_pct=0.02, fee=0.0,
                                      side="long", entry_rsi=30.0, exit_rsi=55.0)
            trm, eqm, _, _ = backtest(w, capital=cap, stop_pct=0.02, fee=-0.00005,
                                      side="long", entry_rsi=30.0, exit_rsi=55.0)
            g, ntr, win = stats(tr0, eq0, cap)
            m, _, _ = stats(trm, eqm, cap)
            bh = (w[-1][4] / w[0][4] - 1) * 100
            g_list.append(g)
            m_list.append(m)
            print(f"  {start:>20} {ntr:>7} {win:>5.1f}% {g:>+7.2f}% "
                  f"{m:>+11.2f}% {bh:>+6.2f}%")
        if g_list:
            print(f"  {'mean':>20} {'':>7} {'':>6} {np.mean(g_list):>+7.2f}% "
                  f"{np.mean(m_list):>+11.2f}%")
    return data


def part_b(btc_data):
    print("\n" + "=" * 64)
    print("PART B - Realistic maker fills (most recent 90 days, BTC)")
    print("=" * 64)
    cap = 5000.0
    # reuse the last 90 days of the BTC series fetched in Part A
    cutoff = btc_data[-1][0] - 90 * 86400 * 1000
    c = [x for x in btc_data if x[0] >= cutoff]
    print(f"BTC/USDT 1m, {len(c):,} candles\n")

    # Idealized ceiling: full fills, maker both legs -0.005%
    tr, eq, _, _ = backtest(c, capital=cap, stop_pct=0.02, fee=-0.00005,
                            side="long", entry_rsi=30.0, exit_rsi=55.0)
    net, ntr, win = stats(tr, eq, cap)
    print(f"{'model':>34} {'sig':>5} {'fills':>6} {'miss%':>6} "
          f"{'trades':>7} {'win%':>6} {'net%':>8}")
    print(f"{'IDEAL full-fill maker -0.005%':>34} {ntr:>5} {ntr:>6} "
          f"{0.0:>5.0f}% {ntr:>7} {win:>5.1f}% {net:>+7.2f}%")

    # Realistic: passive limit, entry+TP maker -0.005%, stop taker +0.02%
    for fw in (1, 3):
        tr, eq, sig, fl, ms = backtest_maker(
            c, cap, 0.02, 30.0, 55.0,
            maker_fee=-0.00005, taker_fee=0.0002, fill_window=fw)
        net, ntr, win = stats(tr, eq, cap)
        miss = ms / sig * 100 if sig else 0
        print(f"{'REAL fill (window=' + str(fw) + ' bar)':>34} {sig:>5} {fl:>6} "
              f"{miss:>5.1f}% {ntr:>7} {win:>5.1f}% {net:>+7.2f}%")

    # Realistic but ZERO net fee, to isolate the fill-model cost from the rebate
    tr, eq, sig, fl, ms = backtest_maker(
        c, cap, 0.02, 30.0, 55.0,
        maker_fee=0.0, taker_fee=0.0, fill_window=1)
    net, ntr, win = stats(tr, eq, cap)
    miss = ms / sig * 100 if sig else 0
    print(f"{'REAL fill (window=1) ZERO fee':>34} {sig:>5} {fl:>6} "
          f"{miss:>5.1f}% {ntr:>7} {win:>5.1f}% {net:>+7.2f}%")


def part_c(data):
    print("\n" + "=" * 64)
    print("PART C - Maker fills + EMA200 regime filter, 4 x 90-day windows")
    print("        (entry+TP maker -0.005%, stop taker +0.02%, long only)")
    print("=" * 64)
    n_win, wdays, cap = 4, 90, 5000.0
    mk = dict(capital=cap, stop_pct=0.02, entry_rsi=30.0, exit_rsi=55.0,
              maker_fee=-0.00005, taker_fee=0.0002, fill_window=1)
    for sym in ("BTC/USDT", "ETH/USDT"):
        print(f"\n{sym}")
        wins = split_windows(data[sym], n_win, wdays)
        print(f"  {'window':>12} | {'no-filter trades':>16} {'net':>8} "
              f"| {'EMA200 trades':>14} {'net':>8}")
        nf_nets, f_nets = [], []
        for w in wins:
            if len(w) < 1000:
                continue
            start = datetime.fromtimestamp(w[0][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            _, eq0, _, fl0, _ = backtest_maker(w, regime_ema=0, **mk)
            _, eqf, _, flf, _ = backtest_maker(w, regime_ema=200, **mk)
            net0 = (eq0 / cap - 1) * 100
            netf = (eqf / cap - 1) * 100
            nf_nets.append(net0)
            f_nets.append(netf)
            print(f"  {start:>12} | {fl0:>16} {net0:>+7.2f}% "
                  f"| {flf:>14} {netf:>+7.2f}%")
        if nf_nets:
            print(f"  {'mean':>12} | {'':>16} {np.mean(nf_nets):>+7.2f}% "
                  f"| {'':>14} {np.mean(f_nets):>+7.2f}%")
            print(f"  {'worst':>12} | {'':>16} {min(nf_nets):>+7.2f}% "
                  f"| {'':>14} {min(f_nets):>+7.2f}%")


if __name__ == "__main__":
    data = part_a()
    part_b(data["BTC/USDT"])
    part_c(data)
