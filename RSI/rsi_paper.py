#!/usr/bin/env python3
"""Forward paper-trader for the BTC 1m RSI(30->55)+EMA200 maker config.

Measures the real maker limit fill rate that OHLCV backtesting could not.
See docs/superpowers/specs/2026-06-18-rsi-maker-paper-trader-design.md
"""

import json
import time as _time

import numpy as np
from dataclasses import dataclass

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


# ─── Order types + fill engine ───────────────────────────────────────────────

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


# ─── Journal (event log + metric counters + state persistence) ────────────────

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
