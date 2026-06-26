#!/usr/bin/env python3
"""Forward paper-trader for the 4h RSI trendline-break signal (BTC + ETH).

Trades the OOS+drift-validated config: every break (no EMA200 filter), taker
entry at the break bar close, TP+3% / -2% stop / 8-day (48-bar) cap; logs a
hold-48 shadow alongside. See docs/superpowers/specs/2026-06-26-rsi-4h-forward-test-design.md
"""

from dataclasses import dataclass, field

import numpy as np

from rsi_dashboard import calculate_rsi, find_pivots, detect_break  # NB: dashboard's RSI
from rsi_backtest import fetch_ohlcv

# ─── Config (locked from validation) ─────────────────────────────────────────
ASSETS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAME = "4h"
TP_PCT = 0.03
STOP_PCT = 0.02
CAP_BARS = 48          # 8-day live cap (and shadow hold horizon)
FEE = 0.0002           # 0.02%/side taker
CAPITAL = 5000.0       # notional per trade
SEED_BARS = 300        # candles to fetch per poll (RSI warmup + recent pivots)


def latest_break(candles):
    """Return the break on the latest CLOSED bar, or None.

    candles: list of [ts, o, h, l, c, v] (closed bars, oldest first).
    Faithful to the backtest scanner: find_pivots returns only pivots confirmed
    `PIVOT_RIGHT` bars back, so all are 'available' at the last index; detect_break
    fits the last-2-pivot trendline exactly as in the 2y scan.
    """
    if len(candles) < 30:
        return None
    closes = np.array([c[4] for c in candles], dtype=float)
    rsi = calculate_rsi(closes)
    highs, lows = find_pivots(rsi)            # default 3-bar pivots
    idx = len(closes) - 1
    bt, bv = detect_break(rsi, highs, lows, idx)
    if bt is None:
        return None
    side = "LONG" if bt == "bullish_break" else "SHORT"
    return {"side": side, "ts": candles[idx][0], "close": candles[idx][4],
            "rsi": float(rsi[idx]), "tl": float(bv),
            "cleared_by": abs(float(rsi[idx]) - float(bv))}
