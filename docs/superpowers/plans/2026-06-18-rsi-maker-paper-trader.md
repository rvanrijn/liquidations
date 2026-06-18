# RSI Maker Paper Trader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a foreground async paper trader that forward-tests the validated BTC 1m RSI(30→55)+EMA200 maker config against the live Binance trade stream to measure the real maker fill rate.

**Architecture:** A single self-contained async module `RSI/rsi_paper.py` with five pure/thin units (Indicators, FillSim, Strategy, Journal, Feed) reusing `calculate_rsi`/`ema` from `RSI/rsi_backtest.py`. The pure core (Indicators, FillSim, Strategy, Journal) is TDD'd with synthetic data and a fake feed; the thin websocket Feed is verified live. State persists to JSONL + a JSON state file for resume.

**Tech Stack:** Python 3.11, asyncio, `websockets` (existing dep, see `src/clients/base.py`), `ccxt` (transitively, via `rsi_backtest.fetch_ohlcv` — already installed), `numpy`, `pytest` + `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-06-18-rsi-maker-paper-trader-design.md` — read it before starting.

---

## File Structure

| File | Responsibility |
|---|---|
| `RSI/rsi_paper.py` | All five units + CLI/main. New file. |
| `tests/rsi/conftest.py` | Put `RSI/` on `sys.path` so tests import `rsi_paper`. New. |
| `tests/rsi/test_indicators.py` | Indicators unit tests. New. |
| `tests/rsi/test_fillsim.py` | FillSim fill-rule tests. New. |
| `tests/rsi/test_strategy.py` | Strategy state-machine tests (fake feed). New. |
| `tests/rsi/test_journal.py` | Journal metrics/persistence tests. New. |

Runtime artifacts (gitignored, created at runtime, NOT in repo): `RSI/data/rsi_paper_events.jsonl`, `RSI/data/rsi_paper_state.json`.

Within `RSI/rsi_paper.py`, order the code top-to-bottom: config constants → dataclasses (`RestingOrder`, `Fill`, `Position`) → `Indicators` → `FillSim` → `Journal` → `Strategy` → `Feed` → `main()`/CLI.

---

## Task 1: Test harness + module skeleton

**Files:**
- Create: `RSI/rsi_paper.py`
- Create: `tests/rsi/conftest.py`
- Create: `tests/rsi/__init__.py` (empty)

- [ ] **Step 1: Add gitignore entries for runtime artifacts**

Append to `.gitignore`:
```
RSI/data/rsi_paper_events.jsonl
RSI/data/rsi_paper_state.json
RSI/data/cache_*.json
```

- [ ] **Step 2: Create the conftest that exposes the RSI dir to tests**

`tests/rsi/conftest.py`:
```python
import pathlib
import sys

# RSI/ is a top-level dir (not a package); make rsi_paper / rsi_backtest importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "RSI"))
```
Create empty `tests/rsi/__init__.py`.

- [ ] **Step 3: Create the module skeleton with config**

`RSI/rsi_paper.py`:
```python
#!/usr/bin/env python3
"""Forward paper-trader for the BTC 1m RSI(30->55)+EMA200 maker config.

Measures the real maker limit fill rate that OHLCV backtesting could not.
See docs/superpowers/specs/2026-06-18-rsi-maker-paper-trader-design.md
"""

from dataclasses import dataclass, field

from rsi_backtest import calculate_rsi, ema  # same-dir import (see conftest / __main__)

# ─── Config (locked from the validated backtest; CLI-overridable) ────────────
SYMBOL = "BTCUSDT"
RSI_PERIOD = 14
EMA_PERIOD = 200
ENTRY_RSI = 30.0
EXIT_RSI = 55.0          # NB: overrides rsi_backtest default of 50
STOP_PCT = 0.02
CAPITAL = 5000.0
MAKER_FEE = -0.00005     # -0.005%/side rebate
TAKER_FEE = 0.0002       # +0.02%/side (stop leg)
ENTRY_TIF_SEC = 60
SEED_BARS = 1000         # EMA200 ~5x warmup
```

- [ ] **Step 4: Verify it imports**

Run: `cd /Users/rvanrijn/Developer/test/liquidations && python -c "import sys, pathlib; sys.path.insert(0,'RSI'); import rsi_paper; print('ok', rsi_paper.EXIT_RSI)"`
Expected: `ok 55.0`

- [ ] **Step 5: Commit**
```bash
git add RSI/rsi_paper.py tests/rsi/conftest.py tests/rsi/__init__.py .gitignore
git commit -m "feat(rsi-paper): module skeleton + test harness"
```

---

## Task 2: Indicators unit

A rolling close buffer that recomputes RSI(14) and EMA(200) using the backtest functions, so live values match the backtest given the same series.

**Files:**
- Modify: `RSI/rsi_paper.py` (add `Indicators`)
- Create: `tests/rsi/test_indicators.py`

- [ ] **Step 1: Write the failing tests**

`tests/rsi/test_indicators.py`:
```python
import numpy as np
from rsi_backtest import calculate_rsi, ema
from rsi_paper import Indicators


def _ramp(n):
    # deterministic non-monotonic series so RSI is well-defined
    return [100 + 10 * np.sin(i / 5.0) for i in range(n)]


def test_not_ready_until_ema_period():
    ind = Indicators(rsi_period=14, ema_period=200)
    ind.seed(_ramp(150))
    assert ind.ready is False
    ind.seed(_ramp(1000))
    assert ind.ready is True


def test_rsi_ema_match_backtest_functions():
    closes = _ramp(1000)
    ind = Indicators(rsi_period=14, ema_period=200)
    ind.seed(closes)
    arr = np.array(closes, dtype=float)
    assert ind.rsi == calculate_rsi(arr, 14)[-1]
    assert ind.ema == ema(arr, 200)[-1]


def test_update_appends_and_recomputes():
    closes = _ramp(1000)
    ind = Indicators(rsi_period=14, ema_period=200)
    ind.seed(closes)
    ind.update(123.0)
    arr = np.array(closes + [123.0], dtype=float)
    assert ind.rsi == calculate_rsi(arr, 14)[-1]
    assert ind.ema == ema(arr, 200)[-1]


def test_buffer_is_bounded():
    ind = Indicators(rsi_period=14, ema_period=200, max_buffer=1200)
    ind.seed(_ramp(1200))
    for i in range(500):
        ind.update(100.0 + i)
    assert len(ind._closes) <= 1200
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/rvanrijn/Developer/test/liquidations && python -m pytest tests/rsi/test_indicators.py -v`
Expected: FAIL — `ImportError: cannot import name 'Indicators'`.

- [ ] **Step 3: Implement `Indicators`**

Add to `RSI/rsi_paper.py`:
```python
import numpy as np


class Indicators:
    """Rolling close buffer; RSI(14)+EMA(200) via the backtest functions."""

    def __init__(self, rsi_period=RSI_PERIOD, ema_period=EMA_PERIOD, max_buffer=None):
        self.rsi_period = rsi_period
        self.ema_period = ema_period
        # keep enough history for stable EMA; default ~5x period + slack
        self.max_buffer = max_buffer or (ema_period * 5 + 100)
        self._closes: list[float] = []

    def seed(self, closes):
        self._closes = list(closes)[-self.max_buffer:]

    def update(self, close):
        self._closes.append(float(close))
        if len(self._closes) > self.max_buffer:
            self._closes = self._closes[-self.max_buffer:]

    @property
    def ready(self):
        return len(self._closes) >= self.ema_period

    @property
    def rsi(self):
        return calculate_rsi(np.array(self._closes, dtype=float), self.rsi_period)[-1]

    @property
    def ema(self):
        return ema(np.array(self._closes, dtype=float), self.ema_period)[-1]
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/rsi/test_indicators.py -v`
Expected: 4 passed. (Note: `test_buffer_is_bounded` trims to `max_buffer`; with default `max_buffer` larger, `seed` already trims — fine.)

- [ ] **Step 5: Commit**
```bash
git add RSI/rsi_paper.py tests/rsi/test_indicators.py
git commit -m "feat(rsi-paper): Indicators unit (RSI/EMA reuse, bounded buffer)"
```

---

## Task 3: Order types + FillSim

Resting orders and the fill engine. Fill rule is the conservative spec convention; **fills are booked at the order's own limit price**.

**Files:**
- Modify: `RSI/rsi_paper.py` (add `RestingOrder`, `Fill`, `FillSim`)
- Create: `tests/rsi/test_fillsim.py`

- [ ] **Step 1: Write the failing tests**

`tests/rsi/test_fillsim.py`:
```python
from rsi_paper import RestingOrder, FillSim


def test_buy_fills_at_or_below_limit_at_limit_price():
    fs = FillSim()
    fs.place(RestingOrder(side="BUY", price=100.0, kind="ENTRY", placed_ts=0, expiry_ts=60))
    assert fs.check(101.0, ts=1) == []           # above limit -> no fill
    fills = fs.check(99.5, ts=2)                  # traded through -> fill
    assert len(fills) == 1
    assert fills[0].kind == "ENTRY"
    assert fills[0].price == 100.0               # AT LIMIT, not 99.5


def test_buy_fills_exactly_at_limit():
    fs = FillSim()
    fs.place(RestingOrder(side="BUY", price=100.0, kind="ENTRY", placed_ts=0, expiry_ts=60))
    fills = fs.check(100.0, ts=1)
    assert len(fills) == 1 and fills[0].price == 100.0


def test_entry_expires_after_tif():
    fs = FillSim()
    fs.place(RestingOrder(side="BUY", price=100.0, kind="ENTRY", placed_ts=0, expiry_ts=60))
    expired = fs.expire(now_ts=61)               # past expiry
    assert [o.kind for o in expired] == ["ENTRY"]
    assert fs.check(99.0, ts=62) == []           # gone, no fill


def test_sell_exit_fills_at_or_above_limit_at_limit_price():
    fs = FillSim()
    fs.place(RestingOrder(side="SELL", price=110.0, kind="EXIT", placed_ts=0, expiry_ts=None))
    assert fs.check(109.0, ts=1) == []
    fills = fs.check(110.5, ts=2)
    assert len(fills) == 1 and fills[0].kind == "EXIT" and fills[0].price == 110.0


def test_stop_fills_at_or_below_stop_at_stop_price():
    fs = FillSim()
    fs.place(RestingOrder(side="SELL", price=98.0, kind="STOP", placed_ts=0, expiry_ts=None))
    fills = fs.check(97.5, ts=1)
    assert len(fills) == 1 and fills[0].kind == "STOP" and fills[0].price == 98.0


def test_stop_precedence_over_exit_on_same_print():
    fs = FillSim()
    fs.place(RestingOrder(side="SELL", price=110.0, kind="EXIT", placed_ts=0, expiry_ts=None))
    fs.place(RestingOrder(side="SELL", price=98.0, kind="STOP", placed_ts=0, expiry_ts=None))
    # an impossible-but-defensive print that satisfies both: stop must win, only one fill
    fills = fs.check(97.0, ts=1)
    assert len(fills) == 1 and fills[0].kind == "STOP"


def test_cancel_by_kind():
    fs = FillSim()
    fs.place(RestingOrder(side="SELL", price=110.0, kind="EXIT", placed_ts=0, expiry_ts=None))
    fs.cancel("EXIT")
    assert fs.check(120.0, ts=1) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/rsi/test_fillsim.py -v`
Expected: FAIL — `ImportError: cannot import name 'RestingOrder'`.

- [ ] **Step 3: Implement order types + FillSim**

Add to `RSI/rsi_paper.py`:
```python
@dataclass
class RestingOrder:
    side: str            # "BUY" | "SELL"
    price: float
    kind: str            # "ENTRY" | "EXIT" | "STOP"
    placed_ts: int
    expiry_ts: int | None = None   # absolute ms; ENTRY only


@dataclass
class Fill:
    kind: str
    price: float         # always the order's limit/stop price
    ts: int


class FillSim:
    """Holds resting orders; decides fills on each live print. Pure logic."""

    def __init__(self):
        self._orders: list[RestingOrder] = []

    def place(self, order: RestingOrder):
        self._orders.append(order)

    def cancel(self, kind: str):
        self._orders = [o for o in self._orders if o.kind != kind]

    def has(self, kind: str) -> bool:
        return any(o.kind == kind for o in self._orders)

    def expire(self, now_ts: int) -> list[RestingOrder]:
        """Remove + return ENTRY orders past their TIF."""
        expired = [o for o in self._orders
                   if o.expiry_ts is not None and now_ts >= o.expiry_ts]
        if expired:
            self._orders = [o for o in self._orders if o not in expired]
        return expired

    def check(self, price: float, ts: int) -> list[Fill]:
        """Return fills triggered by this print. STOP has precedence; at most
        one position-closing fill per print."""
        # stop first (worst-case precedence)
        for o in self._orders:
            if o.kind == "STOP" and price <= o.price:
                self._orders.remove(o)
                return [Fill(kind="STOP", price=o.price, ts=ts)]
        fills = []
        for o in list(self._orders):
            hit = (o.side == "BUY" and price <= o.price) or \
                  (o.side == "SELL" and o.kind == "EXIT" and price >= o.price)
            if hit:
                self._orders.remove(o)
                fills.append(Fill(kind=o.kind, price=o.price, ts=ts))
        return fills
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/rsi/test_fillsim.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**
```bash
git add RSI/rsi_paper.py tests/rsi/test_fillsim.py
git commit -m "feat(rsi-paper): FillSim with fill-at-limit-price + stop precedence"
```

---

## Task 4: Journal (metrics + persistence)

Counters/metrics and the two output files. Keep it I/O-light and pure where possible: metric math is testable, file writes go through small methods.

**Files:**
- Modify: `RSI/rsi_paper.py` (add `Journal`)
- Create: `tests/rsi/test_journal.py`

- [ ] **Step 1: Write the failing tests**

`tests/rsi/test_journal.py`:
```python
import json
from rsi_paper import Journal


def test_counters_and_fill_rates(tmp_path):
    j = Journal(events_path=tmp_path / "e.jsonl", state_path=tmp_path / "s.json")
    j.record("ARM", {})
    j.record("ARM", {})
    j.record("FILL", {"kind": "ENTRY", "ttf_sec": 12})
    j.record("MISS", {})
    # 2 arms, 1 entry fill -> 50%
    assert j.entry_arms == 2
    assert j.entry_fills == 1
    assert j.entry_fill_rate() == 0.5
    assert j.avg_ttf_sec() == 12


def test_exit_fill_and_missed_to_stop():
    j = Journal(events_path=None, state_path=None)
    j.record("EXIT_ARM", {})
    j.record("EXIT_ARM", {})
    j.record("FILL", {"kind": "EXIT"})
    j.record("FILL", {"kind": "STOP", "missed_exit": True})
    assert j.exit_arms == 2
    assert j.exit_fills == 1
    assert j.missed_exit_to_stop == 1


def test_events_appended_to_jsonl(tmp_path):
    ep = tmp_path / "e.jsonl"
    j = Journal(events_path=ep, state_path=tmp_path / "s.json")
    j.record("SIGNAL", {"rsi": 28.4, "price": 64800})
    lines = ep.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "SIGNAL" and rec["rsi"] == 28.4


def test_state_roundtrip(tmp_path):
    sp = tmp_path / "s.json"
    j = Journal(events_path=None, state_path=sp)
    j.save_state({"balance": 5084.0, "position": None})
    assert j.load_state()["balance"] == 5084.0
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/rsi/test_journal.py -v`
Expected: FAIL — `ImportError: cannot import name 'Journal'`.

- [ ] **Step 3: Implement `Journal`**

Add to `RSI/rsi_paper.py`:
```python
import json
import time as _time


class Journal:
    """Append-only event log + state file + in-memory metric counters."""

    def __init__(self, events_path=None, state_path=None):
        self.events_path = events_path
        self.state_path = state_path
        self.entry_arms = self.entry_fills = 0
        self.exit_arms = self.exit_fills = 0
        self.missed_exit_to_stop = 0
        self.trades = 0
        self._ttf: list[float] = []

    def record(self, event: str, data: dict):
        if event == "ARM":
            self.entry_arms += 1
        elif event == "EXIT_ARM":
            self.exit_arms += 1
        elif event == "MISS":
            pass
        elif event == "FILL":
            if data.get("kind") == "ENTRY":
                self.entry_fills += 1
                if "ttf_sec" in data:
                    self._ttf.append(data["ttf_sec"])
            elif data.get("kind") == "EXIT":
                self.exit_fills += 1
                self.trades += 1
            elif data.get("kind") == "STOP":
                self.trades += 1
                if data.get("missed_exit"):
                    self.missed_exit_to_stop += 1
        if self.events_path is not None:
            rec = {"event": event, **data}
            with open(self.events_path, "a") as f:
                f.write(json.dumps(rec) + "\n")

    def entry_fill_rate(self):
        return self.entry_fills / self.entry_arms if self.entry_arms else 0.0

    def exit_fill_rate(self):
        return self.exit_fills / self.exit_arms if self.exit_arms else 0.0

    def avg_ttf_sec(self):
        return sum(self._ttf) / len(self._ttf) if self._ttf else 0.0

    def save_state(self, state: dict):
        if self.state_path is not None:
            with open(self.state_path, "w") as f:
                json.dump(state, f)

    def load_state(self):
        if self.state_path is None:
            return None
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except FileNotFoundError:
            return None
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/rsi/test_journal.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**
```bash
git add RSI/rsi_paper.py tests/rsi/test_journal.py
git commit -m "feat(rsi-paper): Journal metrics + JSONL/state persistence"
```

---

## Task 5: Strategy state machine

The FLAT→ARMED_ENTRY→LONG→FLAT machine wiring Indicators + FillSim + Journal. Driven by `on_bar_close` and `on_trade`; no network. This is the heart — test every transition.

**Files:**
- Modify: `RSI/rsi_paper.py` (add `Position`, `Strategy`)
- Create: `tests/rsi/test_strategy.py`

- [ ] **Step 1: Write the failing tests**

`tests/rsi/test_strategy.py`:
```python
import numpy as np
from rsi_paper import Strategy, Indicators, FillSim, Journal


def _series_to(target_rsi_low=True, n=1000, base=100.0):
    """Build closes whose final RSI is low (downtrend tail) or high (uptrend tail)."""
    closes = [base + 10 * np.sin(i / 7.0) for i in range(n - 30)]
    tail = ([closes[-1] - i for i in range(1, 31)] if target_rsi_low
            else [closes[-1] + i for i in range(1, 31)])
    return closes + tail


def _mk(closes):
    ind = Indicators(); ind.seed(closes)
    fs = FillSim(); j = Journal(events_path=None, state_path=None)
    s = Strategy(ind, fs, j)
    return s, ind, fs, j


def test_arms_entry_when_rsi_low_and_above_ema():
    closes = _series_to(target_rsi_low=True)
    # force last close above EMA by lifting whole series baseline is hard;
    # instead assert: if rsi<30 and close>ema, an ENTRY order is armed.
    s, ind, fs, j = _mk(closes)
    s.on_bar_close({"close": closes[-1], "ts": 60_000})
    if ind.rsi < 30 and closes[-1] > ind.ema:
        assert fs.has("ENTRY")
        assert j.entry_arms == 1
    else:
        assert not fs.has("ENTRY")  # regime/threshold not met -> no arm


def test_entry_fill_transitions_to_long_and_sets_stop():
    s, ind, fs, j = _mk(_series_to(True))
    # Manually arm to make the test deterministic regardless of EMA position:
    s._force_arm(price=100.0, ts=0)
    fills = s.on_trade(price=99.0, ts=30_000)  # fill at 100.0
    assert s.state == "LONG"
    assert s.position.entry_price == 100.0
    assert abs(s.position.stop_price - 98.0) < 1e-9  # 2% stop
    assert fs.has("STOP")


def test_entry_miss_after_tif_returns_flat():
    s, ind, fs, j = _mk(_series_to(True))
    s._force_arm(price=100.0, ts=0)
    s.on_trade(price=101.0, ts=30_000)          # above limit, no fill
    s.on_bar_close({"close": 101.0, "ts": 61_000})  # past 60s TIF -> expire
    assert s.state == "FLAT"
    assert not fs.has("ENTRY")


def test_tp_path_books_maker_fill_at_limit():
    s, ind, fs, j = _mk(_series_to(True))
    s._force_arm(price=100.0, ts=0)
    s.on_trade(price=100.0, ts=10_000)          # enter long @100
    s._force_exit_arm(price=110.0, ts=20_000)   # RSI>=55 exit limit @110
    s.on_trade(price=111.0, ts=30_000)          # TP fill @110
    assert s.state == "FLAT"
    assert j.exit_fills == 1 and j.trades == 1
    # gross +10% on 5000 notional, plus maker rebate both legs (>0)
    assert s.balance > 5000.0


def test_stop_path_books_taker_and_missed_exit_flag():
    s, ind, fs, j = _mk(_series_to(True))
    s._force_arm(price=100.0, ts=0)
    s.on_trade(price=100.0, ts=10_000)          # long @100, stop @98
    s._force_exit_arm(price=110.0, ts=20_000)   # exit armed but never fills
    s.on_trade(price=97.0, ts=30_000)           # stop fill @98
    assert s.state == "FLAT"
    assert j.missed_exit_to_stop == 1           # exit was armed, stop hit instead
    assert s.balance < 5000.0


def test_gap_while_armed_marks_indeterminate():
    s, ind, fs, j = _mk(_series_to(True))
    s._force_arm(price=100.0, ts=0)
    s.on_gap(now_ts=120_000)
    assert s.state == "FLAT"
    assert not fs.has("ENTRY")
    # arm that was interrupted is NOT counted as a miss
    assert j.entry_arms == 1 and j.entry_fills == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/rsi/test_strategy.py -v`
Expected: FAIL — `ImportError: cannot import name 'Strategy'`.

- [ ] **Step 3: Implement `Position` + `Strategy`**

Add to `RSI/rsi_paper.py`. Provide the `_force_*` test hooks (small, explicit) so transitions are testable without sculpting EMA-precise series.
```python
@dataclass
class Position:
    entry_price: float
    entry_ts: int
    stop_price: float
    notional: float = CAPITAL


class Strategy:
    """FLAT -> ARMED_ENTRY -> LONG -> FLAT. Driven by bar closes + trades."""

    def __init__(self, indicators, fillsim, journal,
                 entry_rsi=ENTRY_RSI, exit_rsi=EXIT_RSI, stop_pct=STOP_PCT,
                 capital=CAPITAL, maker_fee=MAKER_FEE, taker_fee=TAKER_FEE,
                 tif_sec=ENTRY_TIF_SEC):
        self.ind = indicators
        self.fs = fillsim
        self.j = journal
        self.entry_rsi = entry_rsi
        self.exit_rsi = exit_rsi
        self.stop_pct = stop_pct
        self.capital = capital
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.tif_sec = tif_sec
        self.state = "FLAT"
        self.position: Position | None = None
        self.balance = capital
        self._arm_price = 0.0
        self._exit_armed = False

    # ── test/internal hooks ──────────────────────────────────────────────
    def _force_arm(self, price, ts):
        self._arm_price = price
        self.fs.place(RestingOrder("BUY", price, "ENTRY", ts, ts + self.tif_sec * 1000))
        self.state = "ARMED_ENTRY"
        self.j.record("ARM", {"price": price, "ts": ts})

    def _force_exit_arm(self, price, ts):
        self.fs.cancel("EXIT")
        self.fs.place(RestingOrder("SELL", price, "EXIT", ts))
        self._exit_armed = True
        self.j.record("EXIT_ARM", {"price": price, "ts": ts})

    # ── event handlers ───────────────────────────────────────────────────
    def on_bar_close(self, candle):
        close, ts = float(candle["close"]), int(candle["ts"])
        self.ind.update(close)
        if not self.ind.ready:
            return
        if self.state == "ARMED_ENTRY":
            if self.fs.expire(ts):                       # TIF elapsed
                self.state = "FLAT"
                self.j.record("MISS", {"ts": ts})
            return
        if self.state == "FLAT":
            if self.ind.rsi < self.entry_rsi and close > self.ind.ema:
                self.j.record("SIGNAL", {"rsi": self.ind.rsi, "price": close, "ts": ts})
                self._force_arm(close, ts)
        elif self.state == "LONG":
            if self.ind.rsi >= self.exit_rsi:
                self._force_exit_arm(close, ts)

    def on_trade(self, price, ts):
        price, ts = float(price), int(ts)
        for fill in self.fs.check(price, ts):
            self._apply_fill(fill, ts)

    def on_gap(self, now_ts):
        # any resting order interrupted by a gap is indeterminate, not a miss
        self.fs.cancel("ENTRY")
        self.fs.cancel("EXIT")
        self.fs.cancel("STOP")
        self.j.record("GAP", {"ts": now_ts})
        self.state = "FLAT"
        self.position = None
        self._exit_armed = False

    # ── fill application ─────────────────────────────────────────────────
    def _apply_fill(self, fill, ts):
        if fill.kind == "ENTRY":
            ttf = (ts - self._arm_ts()) / 1000.0 if self._arm_ts() else 0.0
            self.position = Position(
                entry_price=fill.price, entry_ts=ts,
                stop_price=fill.price * (1 - self.stop_pct))
            self.balance *= (1 - self.maker_fee)             # entry maker leg
            self.fs.place(RestingOrder("SELL", self.position.stop_price, "STOP", ts))
            self.state = "LONG"
            self._exit_armed = False
            self.j.record("FILL", {"kind": "ENTRY", "price": fill.price,
                                   "ttf_sec": ttf, "ts": ts})
        elif fill.kind in ("EXIT", "STOP"):
            ret = fill.price / self.position.entry_price - 1
            leg_fee = self.maker_fee if fill.kind == "EXIT" else self.taker_fee
            self.balance *= (1 + ret) * (1 - leg_fee)
            self.fs.cancel("STOP"); self.fs.cancel("EXIT")
            self.j.record("FILL", {"kind": fill.kind, "price": fill.price, "ret": ret,
                                   "missed_exit": fill.kind == "STOP" and self._exit_armed,
                                   "ts": ts})
            self.position = None
            self._exit_armed = False
            self.state = "FLAT"

    def _arm_ts(self):
        for o in self.fs._orders:
            if o.kind == "ENTRY":
                return o.placed_ts
        return 0
```
Note: record the arm timestamp before the ENTRY order is removed by `check`. Since `_apply_fill` runs after `check` already popped the order, capture `placed_ts` differently: store `self._arm_placed_ts` in `_force_arm` and use it in `_apply_fill` for `ttf`. Adjust `_force_arm` to set `self._arm_placed_ts = ts`, and `_apply_fill` to use `(ts - self._arm_placed_ts)/1000`. Replace the `_arm_ts()` helper accordingly.

- [ ] **Step 4: Fix the ttf capture per the note, then run tests**

Run: `python -m pytest tests/rsi/test_strategy.py -v`
Expected: 6 passed. If `test_arms_entry_when_rsi_low_and_above_ema` is inconclusive (regime not met by synthetic series), it still asserts the correct branch — passes either way.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/rsi/ -v`
Expected: all pass (Indicators 4, FillSim 7, Journal 4, Strategy 6).

- [ ] **Step 6: Commit**
```bash
git add RSI/rsi_paper.py tests/rsi/test_strategy.py
git commit -m "feat(rsi-paper): Strategy state machine (entry/TP/stop/gap paths)"
```

---

## Task 6: Replay mode (offline end-to-end)

Drive the whole engine from historical 1m candles so we can smoke-test end-to-end and reconcile against the backtest before going live. Replay fills use bar low/high as a proxy — explicitly NOT a fill-rate measurement.

**Files:**
- Modify: `RSI/rsi_paper.py` (add `run_replay`)
- Create: `tests/rsi/test_replay.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/rsi/test_replay.py
from rsi_paper import run_replay


def _down_then_up(n=1000):
    closes = [100.0] * (n - 60)
    closes += [100.0 - i * 0.3 for i in range(30)]   # drop -> RSI low
    closes += [closes[-1] + i * 0.5 for i in range(30)]  # rally -> RSI high
    return closes


def test_replay_runs_and_reports():
    closes = _down_then_up()
    # candles: [ts, open, high, low, close, vol]; proxy fills use low/high
    candles = []
    for i, c in enumerate(closes):
        candles.append([i * 60_000, c, c + 0.5, c - 0.5, c, 1.0])
    result = run_replay(candles)
    assert "balance" in result
    assert result["entry_arms"] >= 0
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/rsi/test_replay.py -v`
Expected: FAIL — `cannot import name 'run_replay'`.

- [ ] **Step 3: Implement `run_replay`**

Add to `RSI/rsi_paper.py`. Seed with the first `EMA_PERIOD` closes, then for each subsequent candle: emit a synthetic "trade" at the bar low then the bar high (proxy) around the bar close so resting BUY/SELL/STOP can trigger, then `on_bar_close`.
```python
def run_replay(candles, **kw):
    ind = Indicators()
    seed_n = min(len(candles), SEED_BARS)
    ind.seed([c[4] for c in candles[:EMA_PERIOD]])
    fs = FillSim(); j = Journal(events_path=None, state_path=None)
    s = Strategy(ind, fs, j, **kw)
    for c in candles[EMA_PERIOD:]:
        ts, _o, hi, lo, close, _v = c
        # proxy intrabar path: low then high (pessimistic for longs)
        s.on_trade(lo, ts); s.on_trade(hi, ts)
        s.on_bar_close({"close": close, "ts": ts})
    return {"balance": s.balance, "trades": j.trades,
            "entry_arms": j.entry_arms, "entry_fills": j.entry_fills,
            "exit_fills": j.exit_fills, "missed_exit_to_stop": j.missed_exit_to_stop}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/rsi/test_replay.py -v`
Expected: 1 passed.

- [ ] **Step 5: Sanity-reconcile against real data (manual)**

Run:
```bash
cd /Users/rvanrijn/Developer/test/liquidations && python -c "
import sys; sys.path.insert(0,'RSI')
from datetime import datetime, timezone, timedelta
from rsi_backtest import fetch_ohlcv
from rsi_paper import run_replay
since=int((datetime.now(tz=timezone.utc)-timedelta(days=30)).timestamp()*1000)
c=fetch_ohlcv('BTC/USDT','1m',since)
print(run_replay(c))
"
```
Expected: a dict with non-zero `entry_arms` and a plausible balance near 5000. This is a smoke test, not a fill-rate measurement (note in output).

- [ ] **Step 6: Commit**
```bash
git add RSI/rsi_paper.py tests/rsi/test_replay.py
git commit -m "feat(rsi-paper): offline replay mode for end-to-end smoke test"
```

---

## Task 7: Feed (live websocket) + seeding + main loop + CLI

The only network unit. Thin: parse messages → call `on_trade`/`on_bar_close`. Follow the `websockets` pattern in `src/clients/base.py` (connect, async-for messages, reconnect with backoff). Verified live, not unit-mocked.

**Files:**
- Modify: `RSI/rsi_paper.py` (add `Feed`, `seed_indicators`, `run_live`, `main`, CLI)

- [ ] **Step 1: Implement REST seeding**

Reuse `fetch_ohlcv` from `rsi_backtest` to pull the last `SEED_BARS` 1m candles:
```python
from rsi_backtest import fetch_ohlcv

def seed_indicators(ind, symbol="BTC/USDT", bars=SEED_BARS):
    import time as _t
    since = int((_t.time() - bars * 60) * 1000)
    candles = fetch_ohlcv(symbol, "1m", since)
    ind.seed([c[4] for c in candles[-bars:]])
    return candles
```

- [ ] **Step 2: Implement `Feed`**

Combined stream URL: `wss://stream.binance.com:9443/stream?streams=btcusdt@kline_1m/btcusdt@aggTrade`. On each message: if `aggTrade` → `on_trade(float(p), int(T))`; if `kline` and `k.x is True` (bar closed) → `on_bar_close({"close": float(k.c), "ts": int(k.T)})`. Reconnect with exponential backoff; on reconnect call a `on_reconnect` callback so the main loop can refetch the gap and call `strategy.on_gap`.
```python
import asyncio
import json as _json
import logging

import websockets

logger = logging.getLogger("rsi_paper")
WS_URL = ("wss://stream.binance.com:9443/stream?"
          "streams=btcusdt@kline_1m/btcusdt@aggTrade")


class Feed:
    def __init__(self, on_trade, on_bar_close, on_reconnect=None, url=WS_URL):
        self.on_trade = on_trade
        self.on_bar_close = on_bar_close
        self.on_reconnect = on_reconnect
        self.url = url

    async def run(self):
        backoff = 1
        while True:
            try:
                async with websockets.connect(self.url, ping_interval=20) as ws:
                    backoff = 1
                    if self.on_reconnect:
                        self.on_reconnect()
                    async for raw in ws:
                        self._dispatch(raw)
            except Exception as e:  # noqa: BLE001 — keep the loop alive
                logger.warning("ws error: %s; reconnecting in %ds", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _dispatch(self, raw):
        try:
            msg = _json.loads(raw)
            data = msg.get("data", msg)
            if data.get("e") == "aggTrade":
                self.on_trade(float(data["p"]), int(data["T"]))
            elif data.get("e") == "kline" and data["k"]["x"]:
                k = data["k"]
                self.on_bar_close({"close": float(k["c"]), "ts": int(k["T"])})
        except Exception as e:  # noqa: BLE001 — never crash on one bad msg
            logger.debug("skip msg: %s", e)
```

- [ ] **Step 3: Implement `run_live` + summary loop + state save**

Wire it together: seed → restore state → build Strategy → start Feed → every 5 min print the summary line and `save_state`. **Crucially, wire the gap handler** (spec requirement): track the last-seen bar ts; on reconnect, REST-refetch candles since then, re-seed indicators, and — only if a position or resting order spanned the gap — call `strategy.on_gap` so the interrupted order is marked indeterminate (not a miss). The summary cadence is driven off the latest trade ts to stay on exchange time.
```python
def summary_line(s, j, last_ts, started_ts):
    days = (last_ts - started_ts) / 86_400_000 if last_ts > started_ts else 0.0
    return (f"{days:.1f}d  trades {j.trades}  "
            f"entryFill {j.entry_fills}/{j.entry_arms} ({j.entry_fill_rate()*100:.0f}%)  "
            f"exitFill {j.exit_fills}/{j.exit_arms} ({j.exit_fill_rate()*100:.0f}%)  "
            f"missedExit->stop {j.missed_exit_to_stop}  "
            f"avgTTF {j.avg_ttf_sec():.0f}s  bal ${s.balance:,.0f}")


async def run_live(args):
    import os
    os.makedirs("RSI/data", exist_ok=True)
    ind = Indicators()
    seed_indicators(ind, bars=SEED_BARS)
    j = Journal(events_path="RSI/data/rsi_paper_events.jsonl",
                state_path="RSI/data/rsi_paper_state.json")
    s = Strategy(ind, FillSim(), j)
    # NOTE: indicators always recomputed from fresh seed; only restore balance here
    prior = j.load_state()
    if prior:
        s.balance = prior.get("balance", s.balance)

    # exchange-time tracking + gap handling
    clock = {"last_ts": 0, "started_ts": 0, "first": True}

    def on_trade(price, ts):
        if clock["first"]:
            clock["started_ts"] = ts; clock["first"] = False
        clock["last_ts"] = ts
        s.on_trade(price, ts)

    def on_bar_close(candle):
        clock["last_ts"] = max(clock["last_ts"], int(candle["ts"]))
        s.on_bar_close(candle)

    first_connect = {"v": True}

    def on_reconnect():
        if first_connect["v"]:           # initial connect is not a gap
            first_connect["v"] = False
            return
        seed_indicators(ind, bars=SEED_BARS)   # refresh indicators across the gap
        if s.state != "FLAT" or s.fs.has("ENTRY"):
            s.on_gap(clock["last_ts"])          # mark interrupted order indeterminate

    feed = Feed(on_trade=on_trade, on_bar_close=on_bar_close, on_reconnect=on_reconnect)
    asyncio.create_task(_summary_loop(s, j, clock))
    await feed.run()


async def _summary_loop(s, j, clock, interval=300):
    while True:
        await asyncio.sleep(interval)
        line = summary_line(s, j, clock["last_ts"], clock["started_ts"])
        logger.info(line)
        print(line, flush=True)
        j.save_state({"balance": s.balance,
                      "position": s.position.__dict__ if s.position else None})
```

- [ ] **Step 4: Implement CLI / `main`**
```python
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(description="BTC RSI maker paper trader")
    p.add_argument("mode", choices=["run", "replay"], default="run", nargs="?")
    p.add_argument("--days", type=int, default=30, help="replay window")
    args = p.parse_args()
    if args.mode == "replay":
        since = int((_time.time() - args.days * 86400) * 1000)
        candles = fetch_ohlcv("BTC/USDT", "1m", since)
        print(run_replay(candles))
    else:
        asyncio.run(run_live(args))


if __name__ == "__main__":
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    main()
```

- [ ] **Step 5: Smoke-test live for ~2 minutes**

Run: `cd /Users/rvanrijn/Developer/test/liquidations && timeout 130 python RSI/rsi_paper.py run`
Expected: seeds, connects, prints trade/bar activity, and emits at least one summary line; no crash. Confirm `RSI/data/rsi_paper_events.jsonl` and `rsi_paper_state.json` are created. (130s > the foreground `sleep` guard is fine here since it's the app itself; if the environment blocks long-running foreground, run it backgrounded and tail the log.)

- [ ] **Step 6: Full suite + commit**

Run: `python -m pytest tests/rsi/ -v`
Expected: all pass.
```bash
git add RSI/rsi_paper.py
git commit -m "feat(rsi-paper): live websocket feed, seeding, run loop, CLI"
```

---

## Task 8: Docs + reconciliation note

- [ ] **Step 1: Add a usage block to the module docstring**

Document: `python RSI/rsi_paper.py run` (live), `python RSI/rsi_paper.py replay --days 30` (offline), where the log/state files live, and that the headline metric is `exitFill` + `missedExit->stop`.

- [ ] **Step 2: Update the memory finding**

Append to `/Users/rvanrijn/.claude/projects/-Users-rvanrijn-Developer-test-liquidations/memory/rsi-mean-reversion-findings.md` a line that the forward paper-trader exists (`RSI/rsi_paper.py`) and what metric it produces, so the next session knows the test is running.

- [ ] **Step 3: Commit**
```bash
git add RSI/rsi_paper.py
git commit -m "docs(rsi-paper): usage notes + memory pointer"
```

---

## Done criteria

- `python -m pytest tests/rsi/ -v` — all green (Indicators, FillSim, Journal, Strategy, Replay).
- `python RSI/rsi_paper.py replay --days 30` prints a plausible result dict.
- `python RSI/rsi_paper.py run` seeds, connects, trades on the live stream, writes the JSONL + state files, and emits the summary line whose `exitFill` / `missedExit->stop` are the numbers the whole investigation was missing.
- Let it run for a multi-week window; analyze `rsi_paper_events.jsonl` to decide whether the maker edge survives real fills.
