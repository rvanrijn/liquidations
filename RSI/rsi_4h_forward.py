#!/usr/bin/env python3
"""Forward paper-trader for the 4h RSI trendline-break signal (BTC + ETH).

Trades the OOS+drift-validated config: every break (no EMA200 filter), taker
entry at the break bar close, TP+3% / -2% stop / 8-day (48-bar) cap; logs a
hold-48 shadow alongside. See docs/superpowers/specs/2026-06-26-rsi-4h-forward-test-design.md

Usage:
  python RSI/rsi_4h_forward.py once     # single poll (deploy on a 4h cron)
  python RSI/rsi_4h_forward.py run      # loop, sleeping to each 4h close
  python RSI/rsi_4h_forward.py status   # print accumulated stats from state
  python RSI/rsi_4h_forward.py replay --asset ETH/USDT --days 365   # offline reconcile

State + log live in RSI/data/ (gitignored):
  rsi_4h_events.jsonl  — every SIGNAL/ENTRY/EXIT/SKIP
  rsi_4h_state.json    — open positions, shadows, counters, last-processed bar ts

Headline metric = live TP+3% net + win rate; the hold-48 shadow net sits beside
it (would "let it run" have beaten the bracket?). One position per asset — breaks
firing while in a position are logged as SKIP. BTC + ETH only (SOL/BNB failed
validation). Deploy via cron (`once` every 4h) or a long-running `run` loop.
"""

import argparse
import json
import os
import time as _time
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


# ─── Journal ─────────────────────────────────────────────────────────────────

class Journal:
    """Append-only JSONL events + counters + state file (book snapshot + last_ts)."""

    def __init__(self, events_path=None, state_path=None):
        self.events_path = events_path; self.state_path = state_path
        self.live_trades = self.live_wins = 0; self.live_net = 0.0
        self.shadow_trades = self.shadow_wins = 0; self.shadow_net = 0.0
        self.skips = 0
        self.by_asset = {}     # asset -> {"trades","wins","net"} for the LIVE leg

    def record(self, event, data):
        if event == "EXIT":
            if data.get("kind") == "live":
                self.live_trades += 1; self.live_net += data["pnl"]
                if data["pnl"] > 0: self.live_wins += 1
                asset = data.get("asset")
                if asset is not None:
                    a = self.by_asset.setdefault(asset, {"trades": 0, "wins": 0, "net": 0.0})
                    a["trades"] += 1; a["net"] += data["pnl"]
                    if data["pnl"] > 0: a["wins"] += 1
            elif data.get("kind") == "shadow":
                self.shadow_trades += 1; self.shadow_net += data["pnl"]
                if data["pnl"] > 0: self.shadow_wins += 1
        elif event == "SKIP":
            self.skips += 1
        if self.events_path is not None:
            with open(self.events_path, "a") as f:
                f.write(json.dumps({"event": event, **data}) + "\n")

    def _counters(self):
        return {"live_trades": self.live_trades, "live_wins": self.live_wins,
                "live_net": self.live_net, "shadow_trades": self.shadow_trades,
                "shadow_wins": self.shadow_wins, "shadow_net": self.shadow_net,
                "skips": self.skips, "by_asset": self.by_asset}

    def save_state(self, book, last_ts):
        if self.state_path is None: return
        with open(self.state_path, "w") as f:
            json.dump({"book": book.snapshot(), "counters": self._counters(),
                       "last_ts": last_ts}, f)

    def load_state(self, book):
        """Restore counters into self and book state into `book`. Returns last_ts dict."""
        if self.state_path is None: return {}
        try:
            with open(self.state_path) as f:
                st = json.load(f)
        except FileNotFoundError:
            return {}
        c = st.get("counters", {})
        for k, v in c.items(): setattr(self, k, v)
        book.restore(st.get("book", {}))
        return st.get("last_ts", {})

    def summary_line(self):
        wr = (self.live_wins / self.live_trades * 100) if self.live_trades else 0.0
        line = (f"trades {self.live_trades}  win {wr:.0f}%  net ${self.live_net:+,.0f}"
                f"  | shadow hold48 ${self.shadow_net:+,.0f}  skipped {self.skips}")
        for asset, a in sorted(self.by_asset.items()):
            awr = (a["wins"] / a["trades"] * 100) if a["trades"] else 0.0
            line += (f"\n    {asset:<10} {a['trades']:>3} trades  {awr:>3.0f}% win"
                     f"  net ${a['net']:+,.0f}")
        return line


# ─── Orchestration ────────────────────────────────────────────────────────────

def process_asset(asset, candles, book, journal, last_ts):
    """One poll for one asset. `candles` = closed bars (oldest first).
    Advances exits over bars newer than last_ts[asset], then enters on the
    latest bar if it's a break and the asset is flat. Returns updated last_ts."""
    if not candles:
        return last_ts
    prev = last_ts.get(asset, 0)
    for c in candles:
        if c[0] <= prev:
            continue
        bar = {"ts": c[0], "high": c[2], "low": c[3], "close": c[4]}
        for tr in book.advance(asset, bar):
            journal.record("EXIT", {"asset": asset, "kind": tr.kind, "side": tr.side,
                                    "reason": tr.reason, "entry": tr.entry_price,
                                    "exit": tr.exit_price, "ret": tr.ret,
                                    "pnl": tr.pnl, "ts": tr.exit_ts})
    latest = candles[-1]
    if latest[0] > prev:
        sig = latest_break(candles)
        if sig is not None:
            journal.record("SIGNAL", {"asset": asset, **sig})
            if book.in_position(asset):
                journal.record("SKIP", {"asset": asset, "side": sig["side"], "ts": sig["ts"]})
            else:
                book.enter(asset, sig["side"], sig["close"], sig["ts"])
                journal.record("ENTRY", {"asset": asset, "side": sig["side"],
                                         "price": sig["close"], "ts": sig["ts"]})
    last_ts[asset] = latest[0]
    return last_ts


# ─── Fetch + CLI ──────────────────────────────────────────────────────────────

def fetch_closed(asset, bars=SEED_BARS):
    """Fetch ~bars closed 4h candles. fetch_ohlcv is since-paged; drop the
    in-progress final candle (its close time is in the future)."""
    since = int((_time.time() - bars * 4 * 3600) * 1000)
    raw = fetch_ohlcv(asset, TIMEFRAME, since)
    now_ms = _time.time() * 1000
    return [c for c in raw if c[0] + 4 * 3600 * 1000 <= now_ms]   # fully closed only


def run_once(events="RSI/data/rsi_4h_events.jsonl", state="RSI/data/rsi_4h_state.json"):
    os.makedirs("RSI/data", exist_ok=True)
    book = PaperBook(); j = Journal(events_path=events, state_path=state)
    last_ts = j.load_state(book)
    for asset in ASSETS:
        try:
            candles = fetch_closed(asset)
        except Exception as e:                      # noqa: BLE001 — skip poll, keep state
            print(f"fetch failed {asset}: {e}"); continue
        last_ts = process_asset(asset, candles, book, j, last_ts)
    j.save_state(book, last_ts)
    print(j.summary_line())


def run_loop():
    while True:
        run_once()
        now = _time.time(); nxt = (now // (4 * 3600) + 1) * 4 * 3600 + 30
        _time.sleep(max(60, nxt - now))


def replay(asset, days):
    """Offline: drive process_asset bar-by-bar over historical 4h candles and
    print realized vs shadow net — reconcile against the backtest TP+3% cell."""
    since = int((_time.time() - days * 86400) * 1000)
    raw = [c for c in fetch_ohlcv(asset, TIMEFRAME, since)]
    book = PaperBook(); j = Journal(); last = {}
    for k in range(30, len(raw)):
        last = process_asset(asset, raw[:k + 1], book, j, last)
    print(f"{asset} replay {days}d: {j.summary_line()}")


def main():
    p = argparse.ArgumentParser(description="4h RSI trendline-break forward test")
    p.add_argument("mode", choices=["once", "run", "replay", "status"], nargs="?", default="once")
    p.add_argument("--asset", default="BTC/USDT")
    p.add_argument("--days", type=int, default=120)
    a = p.parse_args()
    if a.mode == "run": run_loop()
    elif a.mode == "replay": replay(a.asset, a.days)
    elif a.mode == "status":
        b = PaperBook(); j = Journal(state_path="RSI/data/rsi_4h_state.json")
        j.load_state(b); print(j.summary_line())
    else: run_once()


if __name__ == "__main__":
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    main()
