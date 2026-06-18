#!/usr/bin/env python3
"""
Simple RSI mean-reversion backtest.

Strategy
  - Timeframe : 1h
  - Entry     : go LONG when RSI(14) closes < 30
  - Exit      : sell when RSI(14) closes >= 50  (take profit / mean revert)
  - Stop loss : 2% below entry price (intrabar low touch)
  - Capital   : 5000 USD, fully allocated per trade, long-only, one position at a time

Usage
  python rsi_backtest.py --symbol BTC/USDT --months 6
"""

import argparse
from datetime import datetime, timezone, timedelta

import ccxt
import numpy as np


RSI_PERIOD = 14


def calculate_rsi(closes: np.ndarray, period: int = RSI_PERIOD) -> np.ndarray:
    """Wilder's smoothed RSI (same as rsi_dashboard.py)."""
    rsi = np.full(len(closes), np.nan)
    if len(closes) < period + 1:
        return rsi

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    for i in range(period, len(closes)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else np.inf
        rsi[i] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


def fetch_ohlcv(symbol: str, timeframe: str, since_ms: int) -> list:
    """Page through Binance OHLCV from `since_ms` to now."""
    ex = ccxt.binance({"enableRateLimit": True})
    out = []
    cursor = since_ms
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=1000)
        if not batch:
            break
        out.extend(batch)
        cursor = batch[-1][0] + 1
        if len(batch) < 1000:
            break
    # de-dupe by timestamp, keep order
    seen = set()
    uniq = []
    for c in out:
        if c[0] not in seen:
            seen.add(c[0])
            uniq.append(c)
    return uniq


def ema(x: np.ndarray, period: int) -> np.ndarray:
    """Simple EMA, seeded with SMA of first `period` values."""
    out = np.full(len(x), np.nan)
    if len(x) < period:
        return out
    k = 2.0 / (period + 1)
    out[period - 1] = x[:period].mean()
    for i in range(period, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def backtest(candles, capital=5000.0, stop_pct=0.02,
             entry_rsi=30.0, exit_rsi=50.0, fee=0.0,
             side="long", regime_ema=0,
             long_entry=30.0, short_entry=70.0):
    """side:
        'long'     - buy RSI<entry, exit RSI>=exit
        'short'    - sell RSI>entry, cover RSI<=exit
        'adaptive' - regime-switch on EMA: close>EMA -> long the RSI<long_entry
                     dip; close<EMA -> short the RSI>short_entry rip. Requires
                     regime_ema>0.
    regime_ema: for 'long'/'short' acts as a trend filter (trade only when
                aligned with EMA). For 'adaptive' it picks the direction."""
    ts = np.array([c[0] for c in candles], dtype=np.int64)
    high = np.array([c[2] for c in candles], dtype=float)
    low = np.array([c[3] for c in candles], dtype=float)
    close = np.array([c[4] for c in candles], dtype=float)
    rsi = calculate_rsi(close)
    trend = ema(close, regime_ema) if regime_ema else None
    adaptive = side == "adaptive"
    if adaptive and not regime_ema:
        raise ValueError("adaptive mode requires --regime-ema > 0")

    equity = capital
    in_pos = False
    pos_long = True       # direction of the OPEN position
    entry_px = 0.0
    qty = 0.0
    stop_px = 0.0
    entry_i = 0
    trades = []

    for i in range(len(close)):
        if np.isnan(rsi[i]):
            continue
        have_trend = trend is not None and not np.isnan(trend[i])

        if not in_pos:
            if regime_ema and not have_trend:
                continue
            take = False
            if adaptive:
                if close[i] > trend[i] and rsi[i] < long_entry:
                    take, pos_long = True, True
                elif close[i] < trend[i] and rsi[i] > short_entry:
                    take, pos_long = True, False
            elif side == "long":
                pos_long = True
                take = rsi[i] < entry_rsi and ((not regime_ema) or close[i] > trend[i])
            else:  # short
                pos_long = False
                take = rsi[i] > entry_rsi and ((not regime_ema) or close[i] < trend[i])

            if take:
                entry_px = close[i]
                qty = (equity * (1 - fee)) / entry_px
                stop_px = entry_px * (1 - stop_pct) if pos_long else entry_px * (1 + stop_pct)
                in_pos = True
                entry_i = i
        else:
            exit_px = None
            reason = None
            if pos_long:
                if low[i] <= stop_px:
                    exit_px, reason = stop_px, "STOP"
                elif rsi[i] >= exit_rsi:
                    exit_px, reason = close[i], "TP"
            else:
                if high[i] >= stop_px:
                    exit_px, reason = stop_px, "STOP"
                elif rsi[i] <= exit_rsi:
                    exit_px, reason = close[i], "TP"

            if exit_px is not None:
                ret = (exit_px / entry_px - 1) if pos_long else (entry_px / exit_px - 1)
                equity_before = equity
                equity = equity * (1 + ret) * (1 - fee)
                trades.append({
                    "entry_t": ts[entry_i], "exit_t": ts[i],
                    "entry_px": entry_px, "exit_px": exit_px,
                    "side": "L" if pos_long else "S",
                    "ret_pct": ret * 100,
                    "pnl": equity - equity_before,
                    "equity": equity,
                    "bars": i - entry_i, "reason": reason,
                })
                in_pos = False

    return trades, equity, in_pos, (entry_px if in_pos else None)


def fmt_t(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--months", type=int, default=6)
    p.add_argument("--capital", type=float, default=5000.0)
    p.add_argument("--stop-pct", type=float, default=0.02)
    p.add_argument("--fee", type=float, default=0.0, help="per-side fee fraction, e.g. 0.0004")
    p.add_argument("--side", choices=["long", "short", "adaptive"], default="long")
    p.add_argument("--entry-rsi", type=float, default=None,
                   help="default 30 for long, 70 for short (ignored for adaptive)")
    p.add_argument("--exit-rsi", type=float, default=50.0)
    p.add_argument("--long-entry", type=float, default=30.0, help="adaptive: long when RSI<this")
    p.add_argument("--short-entry", type=float, default=70.0, help="adaptive: short when RSI>this")
    p.add_argument("--regime-ema", type=int, default=0,
                   help="EMA period; trend filter for long/short, direction picker for adaptive")
    p.add_argument("-v", "--verbose", action="store_true", help="print per-trade rows")
    args = p.parse_args()

    if args.entry_rsi is None:
        args.entry_rsi = 30.0 if args.side == "long" else 70.0
    if args.side == "adaptive" and not args.regime_ema:
        args.regime_ema = 200

    since = datetime.now(tz=timezone.utc) - timedelta(days=args.months * 30)
    since_ms = int(since.timestamp() * 1000)

    print(f"Fetching {args.symbol} {args.timeframe} since {since.strftime('%Y-%m-%d')} ...")
    candles = fetch_ohlcv(args.symbol, args.timeframe, since_ms)
    print(f"Got {len(candles)} candles "
          f"({fmt_t(candles[0][0])} -> {fmt_t(candles[-1][0])} UTC)\n")

    trades, equity, open_pos, open_entry = backtest(
        candles, capital=args.capital, stop_pct=args.stop_pct,
        fee=args.fee, side=args.side, entry_rsi=args.entry_rsi,
        exit_rsi=args.exit_rsi, regime_ema=args.regime_ema,
        long_entry=args.long_entry, short_entry=args.short_entry,
    )

    if not trades:
        print("No trades triggered.")
        return

    show_rows = args.verbose
    if show_rows:
        print(f"{'#':>3} {'entry (UTC)':>16} {'exit (UTC)':>16} {'sd':>3} "
              f"{'in':>8} {'out':>8} {'ret%':>7} {'pnl$':>9} {'bars':>5} {'why':>5}")
        for n, t in enumerate(trades, 1):
            print(f"{n:>3} {fmt_t(t['entry_t']):>16} {fmt_t(t['exit_t']):>16} "
                  f"{t.get('side','L'):>3} {t['entry_px']:>8.0f} {t['exit_px']:>8.0f} "
                  f"{t['ret_pct']:>7.2f} {t['pnl']:>9.2f} {t['bars']:>5} {t['reason']:>5}")

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_ret = (equity / args.capital - 1) * 100
    bh = (candles[-1][4] / candles[0][4] - 1) * 100

    # max drawdown on the trade-by-trade equity curve
    curve = np.array([args.capital] + [t["equity"] for t in trades])
    peak = np.maximum.accumulate(curve)
    max_dd = ((curve - peak) / peak).min() * 100

    longs = [t for t in trades if t.get("side", "L") == "L"]
    shorts = [t for t in trades if t.get("side", "L") == "S"]

    if args.side == "adaptive":
        strat = (f"ADAPTIVE EMA{args.regime_ema}: up->long RSI<{args.long_entry:.0f}, "
                 f"down->short RSI>{args.short_entry:.0f}, exit RSI~{args.exit_rsi:.0f}")
    else:
        op = "<" if args.side == "long" else ">"
        xop = ">=" if args.side == "long" else "<="
        regime = f", EMA{args.regime_ema} regime" if args.regime_ema else ""
        strat = (f"{args.side.upper()} RSI{op}{args.entry_rsi:.0f}, "
                 f"exit RSI{xop}{args.exit_rsi:.0f}{regime}")
    print("\n─── SUMMARY ─────────────────────────────")
    print(f"Strategy   : {strat}, {args.stop_pct*100:.0f}% SL, fee={args.fee*100:.3f}%/side")
    print(f"Trades     : {len(trades)}  (TP {sum(t['reason']=='TP' for t in trades)}, "
          f"STOP {sum(t['reason']=='STOP' for t in trades)})  "
          f"[{len(longs)} long / {len(shorts)} short]")
    print(f"Win rate   : {len(wins)/len(trades)*100:.1f}%  ({len(wins)}W / {len(losses)}L)")
    if wins:
        print(f"Avg win    : {np.mean([t['ret_pct'] for t in wins]):+.2f}%")
    if losses:
        print(f"Avg loss   : {np.mean([t['ret_pct'] for t in losses]):+.2f}%")
    print(f"Avg hold   : {np.mean([t['bars'] for t in trades]):.1f} bars")
    print(f"Start cap  : ${args.capital:,.2f}")
    print(f"End equity : ${equity:,.2f}")
    print(f"Net return : {total_ret:+.2f}%   (${equity-args.capital:+,.2f})")
    print(f"Max DD     : {max_dd:.1f}%  (trade-close equity)")
    print(f"Buy & hold : {bh:+.2f}%")
    if open_pos:
        print(f"NOTE: position still OPEN at end (entered @ {open_entry:.0f}), "
              f"not counted in equity above.")


if __name__ == "__main__":
    main()