# RSI 4h Trendline-Break Forward Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a periodic-poll paper trader that forward-tests the OOS-validated 4h RSI trendline-break signal on BTC + ETH, trading a TP+3%/−2%/8-day bracket and logging a hold-48 shadow.

**Architecture:** One self-contained file `RSI/rsi_4h_forward.py` with four pure/thin units (Scanner, PaperBook, Journal, process_asset) + a fetch/CLI shell. No websocket — at 4h, entries are deterministic bar-close fills, so the tool just polls each close. The core (PaperBook + process_asset) is fully TDD'd with injected candles; fetch/CLI is verified by a replay reconcile + a live smoke run.

**Tech Stack:** Python 3.11, `numpy`, `ccxt` (via reused `fetch_ohlcv`), `pytest`. Reuses `find_pivots`/`detect_break`/`calculate_rsi` from `RSI/rsi_dashboard.py`, `fetch_ohlcv` from `RSI/rsi_backtest.py`, and the `Journal` *pattern* from `RSI/rsi_paper.py`.

**Spec:** `docs/superpowers/specs/2026-06-26-rsi-4h-forward-test-design.md` — read it first.

---

## File Structure

| File | Responsibility |
|---|---|
| `RSI/rsi_4h_forward.py` | All units + fetch/CLI/main. New file. |
| `tests/rsi/test_4h_scanner.py` | Scanner tests. New. |
| `tests/rsi/test_4h_book.py` | PaperBook tests (the core). New. |
| `tests/rsi/test_4h_journal.py` | Journal counters/persistence tests. New. |
| `tests/rsi/test_4h_process.py` | process_asset orchestration + idempotency tests. New. |
| `tests/rsi/conftest.py` | **already exists** (puts `RSI/` on `sys.path`). Reuse as-is. |

Runtime artifacts (gitignored): `RSI/data/rsi_4h_events.jsonl`, `RSI/data/rsi_4h_state.json`.

Code order in `rsi_4h_forward.py`: config → `latest_break` (Scanner) → dataclasses (`Position`/`Shadow`/`ClosedTrade`) → `PaperBook` → `Journal` → `process_asset` → `fetch_closed`/`run_once`/`run_loop`/`replay`/`main`.

**Reuse caveats (from spec review — get these right):** import `calculate_rsi` from `rsi_dashboard` (NOT `rsi_backtest` — they differ); `fetch_ohlcv(symbol, timeframe, since_ms)` is `since`-paged, no `limit`; `Journal` is reused as a pattern with new counters.

---

## Task 1: Skeleton + config

**Files:** Create `RSI/rsi_4h_forward.py`. Modify `.gitignore`.

- [ ] **Step 1: gitignore runtime artifacts**

Append to `.gitignore`:
```
RSI/data/rsi_4h_events.jsonl
RSI/data/rsi_4h_state.json
```

- [ ] **Step 2: Create the module skeleton**

`RSI/rsi_4h_forward.py`:
```python
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
```

- [ ] **Step 3: Verify import**

Run: `cd /Users/rvanrijn/Developer/test/liquidations && python -c "import sys,pathlib;sys.path.insert(0,'RSI');import rsi_4h_forward as m;print('ok',m.TP_PCT,m.CAP_BARS)"`
Expected: `ok 0.03 48`

- [ ] **Step 4: Commit**
```bash
git add RSI/rsi_4h_forward.py .gitignore
git commit -m "feat(rsi-4h-fwd): module skeleton + config"
```

---

## Task 2: Scanner — `latest_break`

Detect the break (if any) on the latest closed bar, faithful to the backtest scanner.

**Files:** Modify `RSI/rsi_4h_forward.py`. Create `tests/rsi/test_4h_scanner.py`.

- [ ] **Step 1: Write the failing tests**

`tests/rsi/test_4h_scanner.py`:
```python
import numpy as np
from rsi_4h_forward import latest_break

def _c(ts, close): return [ts, close, close, close, close, 1.0]

def _series(closes):
    return [_c(i*1000, v) for i, v in enumerate(closes)]

def test_no_break_on_flat_series():
    assert latest_break(_series([100.0]*120)) is None

def test_bullish_break_returns_long():
    # falling RSI that turns up sharply at the end → bullish trendline break
    closes = [100 + 8*np.sin(i/6.0) for i in range(110)]
    closes += [closes[-1]-i for i in range(1,16)]   # push RSI down (descending highs)
    closes += [closes[-1]+i*3 for i in range(1,8)]  # sharp rally → cross resistance
    sig = latest_break(_series(closes))
    # may or may not fire depending on pivots; if it fires it must be well-formed
    if sig is not None:
        assert sig["side"] in ("LONG", "SHORT")
        assert sig["ts"] == (len(closes)-1)*1000
        assert sig["close"] == closes[-1]
        assert sig["cleared_by"] >= 0

def test_matches_backtest_detect_break():
    # latest_break at the last bar must equal calling detect_break directly
    from rsi_dashboard import calculate_rsi, find_pivots, detect_break
    closes = [100 + 10*np.sin(i/5.0) for i in range(200)]
    cs = _series(closes)
    arr = np.array(closes)
    rsi = calculate_rsi(arr); hi, lo = find_pivots(rsi)
    bt, bv = detect_break(rsi, hi, lo, len(arr)-1)
    sig = latest_break(cs)
    if bt is None:
        assert sig is None
    else:
        assert sig["side"] == ("LONG" if bt == "bullish_break" else "SHORT")
        assert abs(sig["tl"] - bv) < 1e-9
```

- [ ] **Step 2: Run — expect FAIL** (`cannot import name 'latest_break'`)
Run: `python -m pytest tests/rsi/test_4h_scanner.py -v`

- [ ] **Step 3: Implement `latest_break`**

Add to `RSI/rsi_4h_forward.py` (after config):
```python
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
```

- [ ] **Step 4: Run — expect PASS** (3 passed)
- [ ] **Step 5: Commit**
```bash
git add RSI/rsi_4h_forward.py tests/rsi/test_4h_scanner.py
git commit -m "feat(rsi-4h-fwd): Scanner latest_break (faithful to backtest)"
```

---

## Task 3: PaperBook — the core

One position per asset; live bracket exit (stop→TP→cap) + parallel hold-48 shadow; serializable. **This is the heart — test every path.**

**Files:** Modify `RSI/rsi_4h_forward.py`. Create `tests/rsi/test_4h_book.py`.

- [ ] **Step 1: Write the failing tests**

`tests/rsi/test_4h_book.py`:
```python
from rsi_4h_forward import PaperBook

def bar(ts, hi, lo, close): return {"ts": ts, "high": hi, "low": lo, "close": close}

def test_enter_opens_one_position_and_sets_levels():
    b = PaperBook()
    p = b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    assert b.in_position("BTC/USDT")
    assert abs(p.stop_price - 98.0) < 1e-9 and abs(p.tp_price - 103.0) < 1e-9

def test_second_break_while_in_position_is_skipped():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    assert b.enter("BTC/USDT", "SHORT", 101.0, ts=1) is None
    assert b.skips == 1

def test_long_take_profit_books_win():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    out = b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.2))  # TP 103 touched
    live = [t for t in out if t.kind == "live"][0]
    assert live.reason == "TP" and live.exit_price == 103.0 and live.pnl > 0
    assert not b.in_position("BTC/USDT")

def test_long_stop_before_tp_when_both_touch():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    out = b.advance("BTC/USDT", bar(1, hi=104.0, lo=97.0, close=101.0))  # both hit; stop wins
    live = [t for t in out if t.kind == "live"][0]
    assert live.reason == "STOP" and live.exit_price == 98.0 and live.pnl < 0

def test_short_take_profit():
    b = PaperBook()
    b.enter("ETH/USDT", "SHORT", 100.0, ts=0)
    out = b.advance("ETH/USDT", bar(1, hi=100.5, lo=96.5, close=97.0))  # TP at 97
    live = [t for t in out if t.kind == "live"][0]
    assert live.reason == "TP" and abs(live.exit_price - 97.0) < 1e-9 and live.pnl > 0

def test_cap_closes_after_48_bars_at_close():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    out = []
    for k in range(1, 49):                      # 48 advances, no stop/TP touched
        out += b.advance("BTC/USDT", bar(k, hi=101.0, lo=99.5, close=100.5))
    live = [t for t in out if t.kind == "live"]
    assert len(live) == 1 and live[0].reason == "CAP" and live[0].exit_price == 100.5

def test_shadow_resolves_independently_of_live():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    # bar 1: live TP at 103, but shadow (no TP) keeps running
    b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.0))
    assert not b.in_position("BTC/USDT")              # live closed
    assert len(b.shadow_trades) == 0                  # shadow still open
    # shadow holds to 48 bars
    for k in range(2, 50):
        b.advance("BTC/USDT", bar(k, hi=104.0, lo=99.0, close=103.5))
    assert len(b.shadow_trades) == 1 and b.shadow_trades[0].reason == "HOLD"

def test_fee_applied_both_legs():
    b = PaperBook(fee=0.0002)
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)
    out = b.advance("BTC/USDT", bar(1, hi=103.5, lo=99.5, close=103.2))
    live = [t for t in out if t.kind == "live"][0]
    # gross +3% then two 0.02% legs
    assert abs(live.ret - ((1.03)*(1-0.0002)**2 - 1)) < 1e-9

def test_snapshot_restore_roundtrip():
    b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=5)
    snap = b.snapshot()
    b2 = PaperBook(); b2.restore(snap)
    assert b2.in_position("BTC/USDT")
    assert b2.positions["BTC/USDT"].entry_price == 100.0
    assert len(b2.shadows) == 1
```

- [ ] **Step 2: Run — expect FAIL** (`cannot import name 'PaperBook'`)

- [ ] **Step 3: Implement dataclasses + `PaperBook`**

Add to `RSI/rsi_4h_forward.py`:
```python
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
```

- [ ] **Step 4: Run — expect PASS** (9 passed)
- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest tests/rsi/test_4h_book.py -v` then `python -m pytest tests/rsi/ -q`
```bash
git add RSI/rsi_4h_forward.py tests/rsi/test_4h_book.py
git commit -m "feat(rsi-4h-fwd): PaperBook (bracket + hold-48 shadow, serializable)"
```

---

## Task 4: Journal — events, counters, summary, state IO

**Files:** Modify `RSI/rsi_4h_forward.py`. Create `tests/rsi/test_4h_journal.py`.

- [ ] **Step 1: Write the failing tests**

`tests/rsi/test_4h_journal.py`:
```python
import json
from rsi_4h_forward import Journal, PaperBook

def test_counts_live_and_shadow_separately():
    j = Journal()
    j.record("EXIT", {"kind": "live", "pnl": 120.0})
    j.record("EXIT", {"kind": "live", "pnl": -80.0})
    j.record("EXIT", {"kind": "shadow", "pnl": 200.0})
    j.record("SKIP", {})
    assert j.live_trades == 2 and j.live_wins == 1
    assert abs(j.live_net - 40.0) < 1e-9
    assert j.shadow_trades == 1 and abs(j.shadow_net - 200.0) < 1e-9
    assert j.skips == 1

def test_events_appended(tmp_path):
    ep = tmp_path / "e.jsonl"
    j = Journal(events_path=ep, state_path=tmp_path / "s.json")
    j.record("ENTRY", {"asset": "BTC/USDT", "side": "LONG", "price": 100})
    rec = json.loads(ep.read_text().strip())
    assert rec["event"] == "ENTRY" and rec["asset"] == "BTC/USDT"

def test_state_roundtrip_with_book(tmp_path):
    sp = tmp_path / "s.json"
    j = Journal(state_path=sp); b = PaperBook()
    b.enter("BTC/USDT", "LONG", 100.0, ts=3)
    j.record("EXIT", {"kind": "live", "pnl": 50.0})
    j.save_state(b, last_ts={"BTC/USDT": 999})
    j2 = Journal(state_path=sp); b2 = PaperBook()
    last = j2.load_state(b2)
    assert last["BTC/USDT"] == 999
    assert b2.in_position("BTC/USDT")
    assert j2.live_trades == 1 and abs(j2.live_net - 50.0) < 1e-9
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `Journal`**

Add to `RSI/rsi_4h_forward.py`:
```python
import json


class Journal:
    """Append-only JSONL events + counters + state file (book snapshot + last_ts)."""

    def __init__(self, events_path=None, state_path=None):
        self.events_path = events_path; self.state_path = state_path
        self.live_trades = self.live_wins = 0; self.live_net = 0.0
        self.shadow_trades = self.shadow_wins = 0; self.shadow_net = 0.0
        self.skips = 0

    def record(self, event, data):
        if event == "EXIT":
            if data.get("kind") == "live":
                self.live_trades += 1; self.live_net += data["pnl"]
                if data["pnl"] > 0: self.live_wins += 1
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
                "skips": self.skips}

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
        return (f"trades {self.live_trades}  win {wr:.0f}%  net ${self.live_net:+,.0f}"
                f"  | shadow hold48 ${self.shadow_net:+,.0f}  skipped {self.skips}")
```

- [ ] **Step 4: Run — expect PASS** (3 passed)
- [ ] **Step 5: Commit**
```bash
git add RSI/rsi_4h_forward.py tests/rsi/test_4h_journal.py
git commit -m "feat(rsi-4h-fwd): Journal counters + JSONL + state IO"
```

---

## Task 5: `process_asset` — idempotent orchestration

Pure (candles injected, no network): advance exits over new bars, enter on the latest break, idempotent via `last_ts`.

**Files:** Modify `RSI/rsi_4h_forward.py`. Create `tests/rsi/test_4h_process.py`.

- [ ] **Step 1: Write the failing tests**

`tests/rsi/test_4h_process.py`:
```python
from rsi_4h_forward import PaperBook, Journal, process_asset

def candle(ts, hi, lo, close): return [ts, close, hi, lo, close, 1.0]

def test_entry_on_latest_break(monkeypatch):
    import rsi_4h_forward as m
    cs = [candle(i, 101, 99, 100) for i in range(40)]
    # force a LONG break on the latest bar
    monkeypatch.setattr(m, "latest_break",
        lambda c: {"side": "LONG", "ts": c[-1][0], "close": c[-1][4],
                   "rsi": 40.0, "tl": 36.0, "cleared_by": 4.0})
    b = PaperBook(); j = Journal(); last = {}
    last = process_asset("BTC/USDT", cs, b, j, last)
    assert b.in_position("BTC/USDT")
    assert last["BTC/USDT"] == cs[-1][0]

def test_idempotent_rerun_books_nothing(monkeypatch):
    import rsi_4h_forward as m
    cs = [candle(i, 101, 99, 100) for i in range(40)]
    monkeypatch.setattr(m, "latest_break", lambda c: None)  # no signal
    b = PaperBook(); j = Journal()
    last = process_asset("BTC/USDT", cs, b, j, {})
    snap1 = b.snapshot()
    last = process_asset("BTC/USDT", cs, b, j, last)   # same bars again
    assert b.snapshot() == snap1                        # nothing changed

def test_gap_advances_exits_over_missed_bars(monkeypatch):
    import rsi_4h_forward as m
    monkeypatch.setattr(m, "latest_break", lambda c: None)
    b = PaperBook(); j = Journal()
    b.enter("BTC/USDT", "LONG", 100.0, ts=0)            # open before any poll
    # later poll sees several new bars; one of them hits the -2% stop (low 97.5)
    cs = [candle(1, 101, 99.5, 100), candle(2, 101, 97.5, 99), candle(3, 100, 99, 99.5)]
    process_asset("BTC/USDT", cs, b, j, {"BTC/USDT": 0})
    assert not b.in_position("BTC/USDT")                # stop booked during the gap
    assert j.live_trades == 1

def test_break_while_in_position_logs_skip(monkeypatch):
    import rsi_4h_forward as m
    cs = [candle(i, 101, 99, 100) for i in range(40)]
    monkeypatch.setattr(m, "latest_break",
        lambda c: {"side": "SHORT", "ts": c[-1][0], "close": c[-1][4],
                   "rsi": 60.0, "tl": 64.0, "cleared_by": 4.0})
    b = PaperBook(); j = Journal()
    b.enter("BTC/USDT", "LONG", 100.0, ts=-1)           # already in a position
    process_asset("BTC/USDT", cs, b, j, {"BTC/USDT": -10})
    assert j.skips == 1 and b.in_position("BTC/USDT")
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `process_asset`**

Add to `RSI/rsi_4h_forward.py`:
```python
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
```
Note: `journal.record("SKIP", …)` increments `j.skips`; `book.enter` is only called when flat, so the book's own `skips` stays 0 in normal flow (the SKIP path never calls `enter`). The Journal is the source of truth for the skip count.

- [ ] **Step 4: Run — expect PASS** (4 passed); then `python -m pytest tests/rsi/ -q`
- [ ] **Step 5: Commit**
```bash
git add RSI/rsi_4h_forward.py tests/rsi/test_4h_process.py
git commit -m "feat(rsi-4h-fwd): process_asset orchestration (idempotent, gap-safe)"
```

---

## Task 6: fetch + CLI (`once`/`run`/`replay`/`status`) + live smoke

The only network unit. Thin: fetch closed candles, drive `process_asset`, persist.

**Files:** Modify `RSI/rsi_4h_forward.py`.

- [ ] **Step 1: Implement `fetch_closed` + `run_once` + `replay` + `run_loop` + `main`**

Add to `RSI/rsi_4h_forward.py`:
```python
import os
import time as _time
import argparse


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
        # sleep to a bit past the next 4h boundary
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
```
Import hygiene: move `import os`, `import time as _time`, `import argparse` to the module top with the others.

- [ ] **Step 2: Replay reconcile (manual, needs network)**

Run:
```bash
cd /Users/rvanrijn/Developer/test/liquidations && python RSI/rsi_4h_forward.py replay --asset BTC/USDT --days 365
```
Expected: a summary line with a plausible trades count and a `net $` in the ballpark of the backtest's BTC 4h TP+3% cell (positive over a long window). It's a reconcile aid, not an exact match (replay enters one-at-a-time per asset vs the backtest's independent-trade convention). If network is unavailable, report that and rely on the unit tests.

- [ ] **Step 3: Live smoke (one poll)**

Run: `cd /Users/rvanrijn/Developer/test/liquidations && python RSI/rsi_4h_forward.py once`
Expected: prints a summary line; creates `RSI/data/rsi_4h_state.json`. Run it **twice** and confirm the second run changes nothing it shouldn't (idempotent — no new trades unless a fresh 4h bar closed between runs).

- [ ] **Step 4: Full suite + commit**

Run: `python -m pytest tests/rsi/ -q` (all pass)
```bash
git add RSI/rsi_4h_forward.py
git commit -m "feat(rsi-4h-fwd): fetch + CLI (once/run/replay/status) + main"
```

---

## Task 7: Docs + memory pointer

- [ ] **Step 1: Usage block in the module docstring**

Document: `python RSI/rsi_4h_forward.py once` (cron / single poll), `run` (loop), `replay --asset ETH/USDT --days 365`, `status`; where the log/state files live; headline metric = live TP+3% net + win rate, with hold-48 shadow beside it. Note: deploy as a cron job (every 4h) or a `run` loop.

- [ ] **Step 2: Memory pointer**

Append to `/Users/rvanrijn/.claude/projects/-Users-rvanrijn-Developer-test-liquidations/memory/rsi-mean-reversion-findings.md` a line that the 4h forward tester exists (`RSI/rsi_4h_forward.py`), what it trades (BTC/ETH 4h breaks, TP+3% bracket + hold-48 shadow), and how to run it — so the next session knows the forward test is live.

- [ ] **Step 3: Commit**
```bash
git add RSI/rsi_4h_forward.py
git commit -m "docs(rsi-4h-fwd): usage notes + memory pointer"
```

---

## Done criteria

- `python -m pytest tests/rsi/ -q` — all green (Scanner, PaperBook, Journal, process_asset).
- `python RSI/rsi_4h_forward.py replay --asset BTC/USDT --days 365` prints a plausible, positive-ish summary reconcilable with the backtest BTC 4h TP+3% cell.
- `python RSI/rsi_4h_forward.py once` polls BTC+ETH, writes state + events, prints the summary; re-running is idempotent within a 4h bar.
- Deploy `once` on a 4h cron (or `run` loop); let it accumulate trades; periodically read `rsi_4h_events.jsonl` / `status` to compare live TP+3% vs the hold-48 shadow vs the backtest expectation.
