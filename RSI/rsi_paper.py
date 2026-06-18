#!/usr/bin/env python3
"""Forward paper-trader for the BTC 1m RSI(30->55)+EMA200 maker config.

Measures the real maker limit fill rate that OHLCV backtesting could not.
See docs/superpowers/specs/2026-06-18-rsi-maker-paper-trader-design.md

Usage:
  python RSI/rsi_paper.py run              # live: connect to Binance, paper-trade
  python RSI/rsi_paper.py replay --days 30 # offline smoke test over historical 1m

Live mode writes to RSI/data/ (gitignored):
  rsi_paper_events.jsonl  — every SIGNAL/ARM/FILL/MISS/STOP/GAP (raw record)
  rsi_paper_state.json    — balance + open position (resume)

The headline metrics are exitFill (TP fills / exit opportunities) and
missedExit->stop (exits that round-tripped into the 2% stop) — the numbers the
backtest could only assume. Let it run for weeks, then analyse the JSONL.
Note: replay fills are a low/high proxy, NOT a fill-rate measurement — only the
live trade stream can produce the real fill rate.
"""

import argparse
import asyncio
import json
import logging
import time as _time

import numpy as np
import websockets
from dataclasses import dataclass

from rsi_backtest import calculate_rsi, ema, fetch_ohlcv  # same-dir import (see conftest / __main__)

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


# ─── Position + Strategy state machine ───────────────────────────────────────

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
        self._arm_placed_ts = 0
        self._exit_armed = False

    # ── test/internal hooks ──────────────────────────────────────────────
    def _force_arm(self, price, ts):
        self._arm_placed_ts = ts
        self.fs.place(RestingOrder("BUY", price, "ENTRY", ts, ts + self.tif_sec * 1000))
        self.state = "ARMED_ENTRY"
        self.j.record("ARM", {"price": price, "ts": ts})

    def _force_exit_arm(self, price, ts):
        self.fs.cancel("EXIT")
        self.fs.place(RestingOrder("SELL", price, "EXIT", ts))
        if not self._exit_armed:                      # count once per exit opportunity
            self.j.record("EXIT_ARM", {"price": price, "ts": ts})
        self._exit_armed = True

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
            ttf = (ts - self._arm_placed_ts) / 1000.0
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


# ─── Offline replay mode ──────────────────────────────────────────────────────

def run_replay(candles, **kw):
    """Drive the full engine from historical 1m OHLCV candles for smoke-testing
    and backtest reconciliation.

    Intrabar fill proxy: for each candle we emit the bar LOW then the bar HIGH
    so that resting BUY / SELL / STOP orders can trigger.  This is intentionally
    a rough approximation — it is NOT a fill-rate measurement.  Live maker fills
    depend on queue position and real tick-by-tick price, which OHLCV history
    cannot reproduce.  Use this only to confirm the engine runs end-to-end and
    that the P&L direction is broadly consistent with the backtest.
    """
    ind = Indicators()
    ind.seed([c[4] for c in candles[:EMA_PERIOD]])
    fs = FillSim()
    j = Journal(events_path=None, state_path=None)
    s = Strategy(ind, fs, j, **kw)
    for c in candles[EMA_PERIOD:]:
        ts, _o, hi, lo, close, _v = c
        # proxy intrabar path: low then high (pessimistic for longs)
        s.on_trade(lo, ts)
        s.on_trade(hi, ts)
        s.on_bar_close({"close": close, "ts": ts})
    return {
        "balance": s.balance,
        "trades": j.trades,
        "entry_arms": j.entry_arms,
        "entry_fills": j.entry_fills,
        "exit_fills": j.exit_fills,
        "missed_exit_to_stop": j.missed_exit_to_stop,
    }


# ─── Live feed ────────────────────────────────────────────────────────────────

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
            msg = json.loads(raw)
            data = msg.get("data", msg)
            if data.get("e") == "aggTrade":
                self.on_trade(float(data["p"]), int(data["T"]))
            elif data.get("e") == "kline" and data["k"]["x"]:
                k = data["k"]
                self.on_bar_close({"close": float(k["c"]), "ts": int(k["T"])})
        except Exception as e:  # noqa: BLE001 — never crash on one bad msg
            logger.debug("skip msg: %s", e)


# ─── REST seeding ─────────────────────────────────────────────────────────────

def seed_indicators(ind, symbol="BTC/USDT", bars=SEED_BARS):
    since = int((_time.time() - bars * 60) * 1000)
    candles = fetch_ohlcv(symbol, "1m", since)
    ind.seed([c[4] for c in candles[-bars:]])
    return candles


# ─── Live run loop ────────────────────────────────────────────────────────────

def summary_line(s, j, last_ts, started_ts):
    days = (last_ts - started_ts) / 86_400_000 if last_ts > started_ts else 0.0
    return (f"{days:.1f}d  trades {j.trades}  "
            f"entryFill {j.entry_fills}/{j.entry_arms} ({j.entry_fill_rate()*100:.0f}%)  "
            f"exitFill {j.exit_fills}/{j.exit_arms} ({j.exit_fill_rate()*100:.0f}%)  "
            f"missedExit->stop {j.missed_exit_to_stop}  "
            f"avgTTF {j.avg_ttf_sec():.0f}s  bal ${s.balance:,.0f}")


async def _summary_loop(s, j, clock, interval=300):
    while True:
        await asyncio.sleep(interval)
        line = summary_line(s, j, clock["last_ts"], clock["started_ts"])
        logger.info(line)
        print(line, flush=True)
        j.save_state({"balance": s.balance,
                      "position": s.position.__dict__ if s.position else None})


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
            clock["started_ts"] = ts
            clock["first"] = False
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


# ─── CLI / entry point ────────────────────────────────────────────────────────

def main():
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
    import sys
    import pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    main()
