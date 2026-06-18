#!/usr/bin/env python3
"""Forward paper-trader for the BTC 1m RSI(30->55)+EMA200 maker config.

Measures the real maker limit fill rate that OHLCV backtesting could not.
See docs/superpowers/specs/2026-06-18-rsi-maker-paper-trader-design.md
"""

import numpy as np
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
