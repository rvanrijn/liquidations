# RSI Maker Paper Trader — Design Spec

**Date:** 2026-06-18
**Status:** Approved (design); pending implementation plan
**Author:** Robert van Rijn (with Claude)

## Purpose

Forward paper-trade the one BTC configuration that survived the RSI mean-reversion
investigation, to measure the single number historical OHLCV backtesting could not
resolve: **the real maker-limit fill rate**, especially on the exit leg.

The backtest established that long RSI(14)<30 → exit RSI≥55 on BTC/USDT 1m has a
small, real, but transaction-cost-bound edge. It is net-negative as a taker and only
positive as a maker — and that maker result rests on an unverifiable assumption that
resting limit orders fill. An EMA200 long-only filter removed the crash-quarter tail
on BTC (4/4 positive quarters) at the cost of trade frequency. See memory
`rsi-mean-reversion-findings.md`. This tool replaces the assumption with measurement.

**Success criterion:** after a multi-week run, we can report entry fill rate, exit
fill rate, time-to-fill, and the count of missed exits that round-tripped into the
stop — the data needed to decide whether the maker edge is real in practice.

## Locked Strategy Parameters

Carried verbatim from the validated backtest config (all overridable via CLI flags,
but these are the defaults):

| Param | Value |
|---|---|
| Symbol | BTC/USDT (Binance) |
| Timeframe | 1m (RSI + EMA computed on closed bars) |
| Indicator | RSI(14) Wilder, EMA(200) |
| Entry signal | bar closes with `RSI < 30` AND `close > EMA200` |
| Entry order | resting maker BUY limit at the signal-bar close |
| Entry TIF | 1 bar (60s); cancel + mark MISS if unfilled |
| Exit (TP) | while LONG and `RSI ≥ 55`, resting maker SELL limit at latest close, re-priced each minute |
| Stop | 2% below entry, taker stop-market, always fills |
| Capital | $5,000, long-only, one position at a time |
| Maker fee | -0.005%/side default (rebate; configurable) |
| Taker fee | +0.02%/side default (stop leg; configurable) |

Out of scope (YAGNI): no leverage, no shorts, no multi-symbol, no DB, no live
dashboard, no orderbook queue modelling.

## Architecture — Approach A

A single new async file `RSI/rsi_paper.py` (~400 lines) reusing `calculate_rsi` and
`ema` from `RSI/rsi_backtest.py` so live indicators are byte-for-byte identical to the
backtest. Five focused, independently testable units:

| Unit | Responsibility | Depends on |
|---|---|---|
| `Feed` | Binance combined websocket (`btcusdt@kline_1m` + `btcusdt@aggTrade`); emits `on_bar_close(candle)` and `on_trade(price, ts)`; reconnect w/ exponential backoff | `websockets` (existing dep) |
| `Indicators` | Rolling close buffer; recompute RSI(14)+EMA(200) on each closed bar | `rsi_backtest` funcs |
| `FillSim` | Holds resting orders; on each live print decides fills per the rules below; pure logic, no I/O | — |
| `Strategy` | FLAT→ARMED_ENTRY→LONG→FLAT state machine; arms/cancels orders on bar closes & fills | Indicators, FillSim |
| `Journal` | Append JSONL events; rewrite JSON state file; emit periodic summary line | — |

A `config` block at the top of the file holds the locked params; a small CLI
(`argparse`) exposes overrides and the run/replay modes.

## State Machine & Fill Rules

States: `FLAT → ARMED_ENTRY → LONG → FLAT`.

- **FLAT** — on a 1m bar close, if `RSI < 30 AND close > EMA200`: place resting BUY
  limit at that close; → `ARMED_ENTRY`; start 60s TIF timer.
- **ARMED_ENTRY** — on each live print: if `trade_price ≤ limit` → **FILL** (record
  time-to-fill), → `LONG`. If 60s elapse unfilled → **CANCEL**, record **MISS**, →
  `FLAT`. A fresh RSI<30 bar re-arms.
- **LONG** — set stop at `entry × (1 − 0.02)`. On each bar close, if `RSI ≥ 55`,
  maintain/re-price a resting SELL limit at the latest close (re-priced each minute
  while RSI≥55). Two exit paths per live print:
  - `trade_price ≥ exit_limit` → **TP FILL** (maker fee), close, → `FLAT`.
  - `trade_price ≤ stop` → **STOP FILL** (taker fee, always fills), close, → `FLAT`.

**Fill convention (conservative):** a resting BUY fills only on a print `≤` its price
(market sold into it); a resting SELL on a print `≥` its price. The stop is a
stop-market: any print through it fills immediately as taker.

Stop precedence: if a single print satisfies both stop and a (lower) exit limit, the
stop takes precedence (worst-case assumption).

## Data Flow, Seeding & Reconnect

**Startup:** REST-fetch the last ~250 closed 1m candles (200 for EMA + warmup) → seed
`Indicators` → load `rsi_paper_state.json` if present (restore balance, open position,
resting orders, counters) → open the combined websocket.

**Steady state:**
- `aggTrade` print → update last price → `FillSim.check(price)` → may fill
  entry/exit/stop → `Strategy` transitions → `Journal` logs.
- `kline_1m` close → append close → recompute RSI+EMA → `Strategy` bar logic (arm
  entry / expire TIF / re-price exit / update unrealized MFE & MAE).

**Reconnect & gaps:** websocket drop → exponential backoff reconnect → on resume,
REST-refetch missed candles and emit a `GAP` event. **Gaps are recorded as gaps, not
misses** — a disconnect must never masquerade as "limit didn't fill." If `ARMED_ENTRY`
or holding a resting exit when a gap occurs, that order's outcome is marked
`indeterminate` and excluded from fill-rate stats. Use exchange-supplied timestamps
from messages, not local clock, for all event times.

## Persistence & Metrics

Two files in `RSI/data/` (gitignored):
- `rsi_paper_events.jsonl` — append-only; every SIGNAL, ARM, FILL, MISS, CANCEL, STOP,
  GAP with timestamps and prices. The raw record for later analysis.
- `rsi_paper_state.json` — rewritten on each change: balance, open position, resting
  orders, counters. Enables clean resume.

**Summary line** (every 5 min and on every trade close; also appended to log):
```
9d  trades 12  entryFill 41/47 (87%)  exitFill 33/41 (80%)  missedExit→stop 4
    avgTTF 18s  grossPnL +$71  netPnL(+rebate) +$84  bal $5084
```
`exitFill` and `missedExit→stop` are the decision-driving metrics.

## Error Handling

- Websocket disconnect → backoff reconnect + gap refetch (above).
- Process crash/restart → resume from `rsi_paper_state.json`; emit a GAP for the
  downtime; indeterminate-mark any order that was resting across the gap.
- Malformed/empty websocket message → log + skip, do not crash the loop.
- REST seed failure on startup → retry with backoff; refuse to start trading logic
  until indicators are seeded (never trade on a short/NaN buffer).

## Testing

Pure, deterministic core → real tests (TDD the core):
- **FillSim:** synthetic print sequences assert fill/miss/stop per `≤`/`≥` rules and
  TIF expiry; stop-precedence case.
- **Strategy:** fake feed (no network) drives full FLAT→ARMED→LONG→TP and →STOP paths,
  plus GAP-while-armed → `indeterminate`.
- **Indicators:** assert RSI/EMA equal the `rsi_backtest` functions on the same series
  (guarantees live == backtest).
- **Replay mode** (`--replay <candles>`): pipe historical 1m through the same engine
  offline for end-to-end smoke test and backtest reconciliation. Live trades aren't in
  history, so replay fills use the bar low/high as a proxy — explicitly labelled, NOT a
  fill-rate measurement.

`Feed` itself is thin and verified manually on first live run; no deep network mocking.

## Open Questions / Future Work

- If the forward test shows the maker edge holds, graduate to a `src/rsipaper/` module
  (console script, SQLite, dashboard) per the existing `liqhunt` bot pattern.
- Orderbook queue-position modelling (rejected now as over-engineered) could refine the
  fill estimate further if the simple touch-based fill proves too optimistic.
