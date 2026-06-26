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


# ─── Position / Trade dataclasses ────────────────────────────────────────────

@dataclass
class Position:
    asset: str; side: str; entry_price: float; entry_ts: int
    stop_price: float; tp_price: float; bars: int = 0


@dataclass
class Shadow:
    asset: str; side: str; entry_price: float; entry_ts: int
    stop_price: float; bars: int = 0


@dataclass
class ClosedTrade:
    asset: str; side: str; entry_price: float; exit_price: float
    entry_ts: int; exit_ts: int; reason: str; ret: float; pnl: float; kind: str


# ─── PaperBook ───────────────────────────────────────────────────────────────

class PaperBook:
    """One live position per asset (TP+3/-2/cap) + a parallel hold-48 shadow."""

    def __init__(self, capital=CAPITAL, stop_pct=STOP_PCT, tp_pct=TP_PCT,
                 cap_bars=CAP_BARS, fee=FEE):
        self.capital = capital; self.stop_pct = stop_pct; self.tp_pct = tp_pct
        self.cap_bars = cap_bars; self.fee = fee
        self.positions = {}        # asset -> Position | None
        self.shadows = []          # open Shadow list
        self.live_trades = []; self.shadow_trades = []; self.skips = 0

    def in_position(self, asset):
        return self.positions.get(asset) is not None

    def enter(self, asset, side, price, ts):
        if self.in_position(asset):
            self.skips += 1
            return None
        stop = price * (1 - self.stop_pct) if side == "LONG" else price * (1 + self.stop_pct)
        tp = price * (1 + self.tp_pct) if side == "LONG" else price * (1 - self.tp_pct)
        pos = Position(asset, side, price, ts, stop, tp)
        self.positions[asset] = pos
        self.shadows.append(Shadow(asset, side, price, ts, stop))
        return pos

    def advance(self, asset, bar):
        """Process the live position + any shadows for `asset` on this bar.
        Returns a list of ClosedTrade booked on this bar."""
        out = []
        pos = self.positions.get(asset)
        if pos is not None:
            tr = self._exit_live(pos, bar)
            if tr:
                self.live_trades.append(tr); self.positions[asset] = None; out.append(tr)
        for sh in [s for s in self.shadows if s.asset == asset]:
            tr = self._exit_shadow(sh, bar)
            if tr:
                self.shadow_trades.append(tr); self.shadows.remove(sh); out.append(tr)
        return out

    def _exit_live(self, p, bar):
        p.bars += 1
        h, l, c, ts = bar["high"], bar["low"], bar["close"], bar["ts"]
        if p.side == "LONG":
            if l <= p.stop_price: return self._close(p, "STOP", p.stop_price, ts, "live")
            if h >= p.tp_price:   return self._close(p, "TP", p.tp_price, ts, "live")
        else:
            if h >= p.stop_price: return self._close(p, "STOP", p.stop_price, ts, "live")
            if l <= p.tp_price:   return self._close(p, "TP", p.tp_price, ts, "live")
        if p.bars >= self.cap_bars: return self._close(p, "CAP", c, ts, "live")
        return None

    def _exit_shadow(self, s, bar):
        s.bars += 1
        h, l, c, ts = bar["high"], bar["low"], bar["close"], bar["ts"]
        if s.side == "LONG" and l <= s.stop_price:  return self._close(s, "STOP", s.stop_price, ts, "shadow")
        if s.side == "SHORT" and h >= s.stop_price: return self._close(s, "STOP", s.stop_price, ts, "shadow")
        if s.bars >= self.cap_bars: return self._close(s, "HOLD", c, ts, "shadow")
        return None

    def _close(self, p, reason, exit_price, exit_ts, kind):
        ret = (exit_price / p.entry_price - 1) if p.side == "LONG" else (p.entry_price / exit_price - 1)
        net = (1 + ret) * (1 - self.fee) ** 2 - 1
        return ClosedTrade(p.asset, p.side, p.entry_price, exit_price, p.entry_ts,
                           exit_ts, reason, net, net * self.capital, kind)

    # ── persistence (cron runs are stateless between invocations) ──
    def snapshot(self):
        return {
            "positions": {a: (vars(p) if p else None) for a, p in self.positions.items()},
            "shadows": [vars(s) for s in self.shadows],
            "skips": self.skips,
        }

    def restore(self, snap):
        self.positions = {a: (Position(**d) if d else None)
                          for a, d in snap.get("positions", {}).items()}
        self.shadows = [Shadow(**d) for d in snap.get("shadows", [])]
        self.skips = snap.get("skips", 0)
