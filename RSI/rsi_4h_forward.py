#!/usr/bin/env python3
"""Forward paper-trader for the 4h RSI trendline-break signal (BTC + ETH).

Trades the OOS+drift-validated config: every break (no EMA200 filter), taker
entry at the break bar close, TP+3% / -2% stop / 8-day (48-bar) cap; logs a
hold-48 shadow alongside. See docs/superpowers/specs/2026-06-26-rsi-4h-forward-test-design.md

Usage:
  python RSI/rsi_4h_forward.py once     # single poll (deploy on a 4h cron)
  python RSI/rsi_4h_forward.py run      # loop, sleeping to each 4h close
  python RSI/rsi_4h_forward.py status   # one-line stats + decay line (scriptable)
  python RSI/rsi_4h_forward.py dash     # rich terminal dashboard (cards, gauges, recent)
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
LADDER_FRAC = 0.5      # scale-out shadow: take this fraction at +TP_PCT, run the rest to cap
FEE = 0.0002           # 0.02%/side taker
CAPITAL = 5000.0       # notional per trade
SEED_BARS = 300        # candles to fetch per poll (RSI warmup + recent pivots)
DECAY_LINE_DAYS = 394  # BTC's worst historical equity drawdown lasted 394d then recovered;
                       # a current BTC drawdown beyond this (no new equity high) = likely decay


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
class Ladder:
    """Scale-out shadow: half off at +TP_PCT, the rest runs to cap (−stop on remainder).
    `acc` accumulates sum(frac_i * (1+ret_i)) across realized legs."""
    asset: str; side: str; entry_price: float; entry_ts: int
    stop_price: float; remaining: float = 1.0; acc: float = 0.0
    partial_done: bool = False; bars: int = 0


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
        self.shadows = []          # open Shadow list (hold-48)
        self.ladders = []          # open Ladder list (scale-out)
        self.live_trades = []; self.shadow_trades = []; self.ladder_trades = []; self.skips = 0

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
        self.ladders.append(Ladder(asset, side, price, ts, stop))
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
        for ld in [x for x in self.ladders if x.asset == asset]:
            tr = self._exit_ladder(ld, bar)
            if tr:
                self.ladder_trades.append(tr); self.ladders.remove(ld); out.append(tr)
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

    def _leg_ret(self, side, entry, price):
        return (price / entry - 1) if side == "LONG" else (entry / price - 1)

    def _exit_ladder(self, L, bar):
        """Scale-out: stop (on remainder) first, then the +TP_PCT partial, then cap.
        Books one ClosedTrade(kind='ladder') once fully resolved."""
        L.bars += 1
        h, l, c, ts = bar["high"], bar["low"], bar["close"], bar["ts"]
        tp1 = L.entry_price * (1 + self.tp_pct) if L.side == "LONG" else L.entry_price * (1 - self.tp_pct)
        # stop on the whole remaining (pessimistic — checked before the partial)
        if (L.side == "LONG" and l <= L.stop_price) or (L.side == "SHORT" and h >= L.stop_price):
            L.acc += L.remaining * (1 + self._leg_ret(L.side, L.entry_price, L.stop_price))
            L.remaining = 0.0
            return self._close_ladder(L, "STOP", ts)
        # partial: take LADDER_FRAC off at +TP_PCT
        if not L.partial_done and ((L.side == "LONG" and h >= tp1) or (L.side == "SHORT" and l <= tp1)):
            f = LADDER_FRAC
            L.acc += f * (1 + self._leg_ret(L.side, L.entry_price, tp1))
            L.remaining -= f; L.partial_done = True
        # cap: the runner (remainder) exits at the close
        if L.bars >= self.cap_bars and L.remaining > 1e-9:
            L.acc += L.remaining * (1 + self._leg_ret(L.side, L.entry_price, c))
            L.remaining = 0.0
            return self._close_ladder(L, "CAP", ts)
        return None

    def _close_ladder(self, L, reason, exit_ts):
        net = (1 - self.fee) ** 2 * L.acc - 1
        return ClosedTrade(L.asset, L.side, L.entry_price, L.entry_price, L.entry_ts,
                           exit_ts, reason, net, net * self.capital, "ladder")

    # ── persistence (cron runs are stateless between invocations) ──
    def snapshot(self):
        return {
            "positions": {a: (vars(p) if p else None) for a, p in self.positions.items()},
            "shadows": [vars(s) for s in self.shadows],
            "ladders": [vars(x) for x in self.ladders],
            "skips": self.skips,
        }

    def restore(self, snap):
        self.positions = {a: (Position(**d) if d else None)
                          for a, d in snap.get("positions", {}).items()}
        self.shadows = [Shadow(**d) for d in snap.get("shadows", [])]
        self.ladders = [Ladder(**d) for d in snap.get("ladders", [])]
        self.skips = snap.get("skips", 0)


# ─── Journal ─────────────────────────────────────────────────────────────────

class Journal:
    """Append-only JSONL events + counters + state file (book snapshot + last_ts)."""

    def __init__(self, events_path=None, state_path=None):
        self.events_path = events_path; self.state_path = state_path
        self.live_trades = self.live_wins = 0; self.live_net = 0.0
        self.shadow_trades = self.shadow_wins = 0; self.shadow_net = 0.0
        self.ladder_trades = self.ladder_wins = 0; self.ladder_net = 0.0
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
            elif data.get("kind") == "ladder":
                self.ladder_trades += 1; self.ladder_net += data["pnl"]
                if data["pnl"] > 0: self.ladder_wins += 1
        elif event == "SKIP":
            self.skips += 1
        if self.events_path is not None:
            with open(self.events_path, "a") as f:
                f.write(json.dumps({"event": event, **data}) + "\n")

    def _counters(self):
        return {"live_trades": self.live_trades, "live_wins": self.live_wins,
                "live_net": self.live_net, "shadow_trades": self.shadow_trades,
                "shadow_wins": self.shadow_wins, "shadow_net": self.shadow_net,
                "ladder_trades": self.ladder_trades, "ladder_wins": self.ladder_wins,
                "ladder_net": self.ladder_net, "skips": self.skips, "by_asset": self.by_asset}

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
        gap = self.shadow_net - self.live_net      # hold-48 vs live TP+3
        lgap = self.ladder_net - self.live_net     # scale-out vs live TP+3
        line = (f"trades {self.live_trades}  win {wr:.0f}%  net ${self.live_net:+,.0f}"
                f"  | hold48 ${self.shadow_net:+,.0f} (Δ ${gap:+,.0f})"
                f"  | ladder ${self.ladder_net:+,.0f} (Δ ${lgap:+,.0f})"
                f"  skipped {self.skips}")
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
                pos = book.enter(asset, sig["side"], sig["close"], sig["ts"])
                journal.record("ENTRY", {"asset": asset, "side": sig["side"],
                                         "price": sig["close"], "tp": pos.tp_price,
                                         "sl": pos.stop_price, "ts": sig["ts"]})
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


# ─── Decay-line check (is the BTC 4h edge still alive, or past its worst-ever DD?) ──

def _decay_status(eq, tt, now_ms, line_days):
    """Pure: given a strategy equity curve `eq` at trade-exit times `tt` (ms),
    classify the current drawdown vs the historical-max-recovery line.
    Returns (days_underwater, crossed: bool, message)."""
    ath_i = int(np.argmax(eq))
    if eq[-1] >= eq[ath_i] - 1e-9:
        return 0.0, False, "at equity highs — edge healthy"
    days = (now_ms - tt[ath_i]) / 86_400_000.0
    if days > line_days:
        return days, True, (f"DECAY LINE CROSSED — {days:.0f}d underwater > {line_days}d "
                            f"historical max; consider standing down")
    return days, False, f"drawdown {days:.0f}/{line_days}d ({line_days - days:.0f}d to line)"


def _strategy_trades(asset, years_back=9):
    """Recompute the asset's full 4h strategy track record from market data, driving
    the SAME PaperBook exit logic the live trader uses. Returns the live ClosedTrades."""
    since = int((_time.time() - years_back * 365 * 86400) * 1000)
    c = fetch_ohlcv(asset, TIMEFRAME, since)
    closes = np.array([x[4] for x in c], dtype=float)
    rsi = calculate_rsi(closes)
    highs, lows = find_pivots(rsi)
    hi = [p[0] for p in highs]; li = [p[0] for p in lows]
    sig = {}; ph = pl = 0
    for idx in range(1, len(closes)):
        while ph < len(highs) and hi[ph] <= idx - 3: ph += 1
        while pl < len(lows) and li[pl] <= idx - 3: pl += 1
        bt, _bv = detect_break(rsi, highs[:ph], lows[:pl], idx)
        if bt:
            sig[idx] = "LONG" if bt == "bullish_break" else "SHORT"
    book = PaperBook()
    for idx, row in enumerate(c):
        book.advance(asset, {"ts": row[0], "high": row[2], "low": row[3], "close": row[4]})
        if idx in sig and not book.in_position(asset):
            book.enter(asset, sig[idx], row[4], row[0])
    return book.live_trades


def decay_check(asset="BTC/USDT", line_days=DECAY_LINE_DAYS):
    """One-line decay verdict for `asset`, recomputed live from market data."""
    trades = _strategy_trades(asset)
    if len(trades) < 30:
        return f"{asset} 4h strategy: too few trades to assess"
    tt = np.array([t.exit_ts for t in trades])
    eq = CAPITAL + np.cumsum(np.array([t.pnl for t in trades]))
    _days, crossed, msg = _decay_status(eq, tt, _time.time() * 1000, line_days)
    flag = "⚠ " if crossed else ""
    return f"{flag}{asset} 4h strategy ({len(trades)} trades, eq ${eq[-1]:,.0f}): {msg}"


def _decay_metrics(asset, line_days=DECAY_LINE_DAYS):
    """(days_underwater, crossed, equity, n_trades) for `asset`, or None."""
    trades = _strategy_trades(asset)
    if len(trades) < 30:
        return None
    tt = np.array([t.exit_ts for t in trades])
    eq = CAPITAL + np.cumsum(np.array([t.pnl for t in trades]))
    days, crossed, _msg = _decay_status(eq, tt, _time.time() * 1000, line_days)
    return days, crossed, float(eq[-1]), len(trades)


def _recent_events(path, n=8):
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    out = []
    for ln in lines[-n * 4:]:
        try:
            e = json.loads(ln)
            if e.get("event") in ("ENTRY", "EXIT", "SKIP"):
                out.append(e)
        except Exception:  # noqa: BLE001
            pass
    return out[-n:]


def render_dash(state="RSI/data/rsi_4h_state.json", events="RSI/data/rsi_4h_events.jsonl"):
    """Polished terminal dashboard (rich). Imports rich lazily so the core stays dep-free."""
    from datetime import datetime
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.columns import Columns
    from rich.table import Table
    from rich.text import Text
    from rich import box

    EDGE, FEE, BRASS, GREY = "#2ECC9A", "#E0654F", "#C7972F", "grey42"
    sign = lambda v: EDGE if v > 0 else (FEE if v < 0 else "white")
    con = Console()
    book = PaperBook(); j = Journal(state_path=state); j.load_state(book)
    wr = (j.live_wins / j.live_trades * 100) if j.live_trades else 0.0
    n_open = sum(1 for p in book.positions.values() if p)

    def card(title, big, sub, c):
        b = Text(); b.append(big, style=f"bold {c}"); b.append("\n" + sub, style="dim")
        return Panel(b, title=f"[{GREY}]{title}[/]", box=box.ROUNDED, border_style=GREY, padding=(0, 1))

    cards = Columns([
        card("LIVE P&L", f"${j.live_net:+,.0f}", f"{j.live_trades} trades · {wr:.0f}% win", sign(j.live_net)),
        card("OPEN · SKIPS", f"{n_open} open", f"{j.skips} skipped", BRASS),
    ], equal=True, expand=True)

    # exit shoot-out: live TP+3 vs the two shadows, with Δ vs live
    so = Table(box=box.SIMPLE_HEAVY, expand=True, title="[dim]exit shoot-out · which harvest wins?[/]", title_justify="left")
    for c, ju in [("exit rule", "left"), ("trades", "right"), ("win%", "right"), ("net $", "right"), ("Δ vs live", "right")]:
        so.add_column(c, justify=ju)
    def _wr(w, n): return f"{(w / n * 100) if n else 0:.0f}%"
    so.add_row("live · TP+3% / −2% / 8d", str(j.live_trades), _wr(j.live_wins, j.live_trades),
               Text(f"${j.live_net:+,.0f}", style=sign(j.live_net)), Text("—", style="dim"))
    so.add_row("shadow · hold-48", str(j.shadow_trades), _wr(j.shadow_wins, j.shadow_trades),
               Text(f"${j.shadow_net:+,.0f}", style=sign(j.shadow_net)),
               Text(f"${j.shadow_net - j.live_net:+,.0f}", style=sign(j.shadow_net - j.live_net)))
    so.add_row("shadow · scale-out ½@3", str(j.ladder_trades), _wr(j.ladder_wins, j.ladder_trades),
               Text(f"${j.ladder_net:+,.0f}", style=sign(j.ladder_net)),
               Text(f"${j.ladder_net - j.live_net:+,.0f}", style=sign(j.ladder_net - j.live_net)))

    at = Table(box=box.SIMPLE_HEAVY, expand=True, title="[dim]live P&L by asset[/]", title_justify="left")
    for c, ju in [("asset", "left"), ("trades", "right"), ("win%", "right"), ("net $", "right")]:
        at.add_column(c, justify=ju)
    if j.by_asset:
        for a, d in sorted(j.by_asset.items()):
            awr = (d["wins"] / d["trades"] * 100) if d["trades"] else 0.0
            at.add_row(a, str(d["trades"]), f"{awr:.0f}%", Text(f"${d['net']:+,.0f}", style=sign(d["net"])))
    else:
        at.add_row("—", "0", "—", "$0  [dim](no trades yet)[/]")

    def gauge(label, days, line, eq, crossed):
        frac = min(days / line, 1.0); W = 32; fill = int(round(frac * W))
        c = EDGE if frac < 0.7 else (BRASS if frac < 0.9 else FEE)
        t = Text(); t.append(f"{label:<4} ", style="bold")
        t.append("█" * fill, style=c); t.append("─" * (W - fill), style="grey30")
        t.append(f"  {days:.0f}/{line:.0f}d", style=c)
        t.append("  ⚠ DECAYED" if crossed else "", style=f"bold {FEE}")
        t.append(f"   eq ${eq:,.0f}", style="dim")
        return t

    rows = []
    for a in ASSETS:
        try:
            m = _decay_metrics(a)
            if m:
                days, crossed, eq, _n = m
                rows.append(gauge(a.split("/")[0], days, DECAY_LINE_DAYS, eq, crossed))
        except Exception as e:  # noqa: BLE001
            rows.append(Text(f"{a}: (market data unavailable — {e})", style="dim"))
    decay = Panel(Group(*rows) if rows else Text("computing…", style="dim"),
                  title=f"[{GREY}]decay watch · drawdown days vs the 394-day line[/]",
                  border_style=GREY, box=box.ROUNDED)

    # open positions with their live entry / TP / SL
    open_rows = []
    for a, pos in book.positions.items():
        if not pos:
            continue
        t = Text()
        t.append(f"{a.split('/')[0]:<4} ", style="bold")
        t.append(f"{pos.side:<5} ", style=EDGE if pos.side == "LONG" else FEE)
        t.append(f"entry {pos.entry_price:>9,.0f}  ", style="white")
        t.append(f"TP {pos.tp_price:>9,.0f}  ", style=EDGE)
        t.append(f"SL {pos.stop_price:>9,.0f}  ", style=FEE)
        t.append(f"{pos.bars}/{CAP_BARS}b ({(CAP_BARS - pos.bars) * 4}h left)", style="dim")
        open_rows.append(t)
    open_panel = Panel(Group(*open_rows) if open_rows else Text("flat — no open positions", style="dim"),
                       title=f"[{GREY}]open positions · entry / TP+3% / SL−2%[/]",
                       border_style=GREY, box=box.ROUNDED)

    evs = _recent_events(events)
    rt = Table(box=box.SIMPLE, expand=True, title="[dim]recent[/]", title_justify="left")
    for c, ju in [("time", "left"), ("event", "left"), ("asset", "left"), ("side", "left"), ("detail", "right")]:
        rt.add_column(c, justify=ju)
    for e in evs:
        t = datetime.fromtimestamp(e.get("ts", 0) / 1000).strftime("%m-%d %H:%M")
        ev = e["event"]
        if ev == "EXIT":
            detail = Text(f"${e.get('pnl', 0):+,.0f} {e.get('reason', '')}", style=sign(e.get("pnl", 0)))
            evstyle = sign(e.get("pnl", 0))
        elif ev == "ENTRY":
            txt = f"@ {e.get('price', 0):,.0f}"
            if e.get("tp") and e.get("sl"):
                txt += f"  TP {e['tp']:,.0f} / SL {e['sl']:,.0f}"
            detail = Text(txt); evstyle = EDGE
        else:
            detail = Text("—"); evstyle = "dim"
        rt.add_row(t, Text(ev, style=evstyle), e.get("asset", ""), e.get("side", ""), detail)

    con.print()
    con.rule(f"[bold]RSI 4h FORWARD TEST[/]  ·  BTC + ETH  ·  {datetime.now():%Y-%m-%d %H:%M}", style=BRASS)
    con.print(cards)
    con.print(so)
    con.print(open_panel)
    con.print(at)
    con.print(decay)
    if evs:
        con.print(rt)
    con.print(Text("  the data, not a hunch, makes the next call.", style="dim italic"))


def main():
    p = argparse.ArgumentParser(description="4h RSI trendline-break forward test")
    p.add_argument("mode", choices=["once", "run", "replay", "status", "dash"], nargs="?", default="once")
    p.add_argument("--asset", default="BTC/USDT")
    p.add_argument("--days", type=int, default=120)
    a = p.parse_args()
    if a.mode == "run": run_loop()
    elif a.mode == "dash": render_dash()
    elif a.mode == "replay": replay(a.asset, a.days)
    elif a.mode == "status":
        b = PaperBook(); j = Journal(state_path="RSI/data/rsi_4h_state.json")
        j.load_state(b); print(j.summary_line())
        try:                                            # live decay-line check (needs network)
            print(decay_check("BTC/USDT"))
        except Exception as e:                          # noqa: BLE001 — offline status still works
            print(f"(decay check skipped — {e})")
    else: run_once()


if __name__ == "__main__":
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    main()
