# MCB (VuManChu Cipher B) 8H Heikin-Ashi Backtest — Design

**Date:** 2026-06-19
**Status:** Approved design, pending implementation plan
**Source idea:** `MCBstrat/strategy.md`

## 1. Goal & honest framing

Backtest the strategy exactly as written and answer one question: **is there a real,
cost-survivable edge?** The deliverable is a research report plus a reusable backtest
module — no Pine strategy and no live bot until the edge is proven.

The strategy as stated:

- 8H BTCUSD chart, Heikin Ashi candles.
- **Long:** enter when a MarketCipher B green dot prints and the money-flow ("volume") is
  green; exit when money-flow closes red.
- **Short:** enter when a red dot prints and money-flow is red; exit when money-flow closes green.

### The proprietary-indicator problem

The operator's real MarketCipher B is proprietary and not recomputable from OHLCV (confirmed
in prior sessions; the TV MCP exposes current-bar study values only). However, the rules as
written map **exactly** onto the open-source **VuManChu Cipher B** clone, whose every
component IS computable from OHLCV. We therefore backtest the VuManChu reproduction and
**cross-check it against the live TradingView chart** so the substitution is validated, not
assumed. This directly honors line 1 of the source idea ("check with MCP Tradview").

## 2. Signal definitions (computable)

All indicators are computed on **Heikin Ashi** candles, because that is what the chart
displays and what the indicator reads.

- **WaveTrend** (VuManChu defaults, source HA `hlc3`):
  - `esa = ema(hlc3, 9)`; `d = ema(abs(hlc3 - esa), 9)`; `ci = (hlc3 - esa) / (0.015 * d)`
  - `wt1 = ema(ci, 12)`; `wt2 = sma(wt1, 3)`
- **Green dot** = WaveTrend cross UP (`wt1` crosses above `wt2`) AND `wt2 <= -53` (oversold L1).
- **Red dot** = WaveTrend cross DOWN AND `wt2 >= 53` (overbought L1).
- **Money Flow ("volume")** = `sma(((HAclose - HAopen) / (HAhigh - HAlow)) * 150, 60)`.
  - **Green** = MF > 0; **Red** = MF < 0.
- Oversold/overbought levels (±53) and MF period (60) are the VuManChu defaults and are
  treated as fixed for the primary run. A small documented sensitivity check is allowed but
  parameter optimization is explicitly out of scope (avoid overfit).

### Position state machine (flat-between, never both sides)

- **Flat → Long** when `green_dot AND MF > 0`.
- **Long → Flat** when `MF < 0`.
- **Flat → Short** when `red_dot AND MF < 0`.
- **Short → Flat** when `MF > 0`.
- A long exit does NOT auto-open a short; a short requires an actual red dot. Therefore the
  system spends real time flat. Long and short are mutually exclusive.

## 3. Execution model (the integrity core)

- Signals are confirmed only on the **closed** 8H HA bar `t`.
- Fills happen at the **real (non-HA) open** of bar `t+1`.
- This is non-negotiable: Heikin Ashi close ≠ real market price, so filling at HA prices
  produces fantasy results that cannot be reproduced live.
- **Costs:** 0.1% fees + 0.05% slippage per round-trip (taker, Kraken/Bybit perp realistic).
  The report also includes a **zero-cost column** to isolate the raw signal edge.

## 4. Data

- **Source:** Bybit `BTCUSDT` perpetual is the **canonical, single source** — the cost model
  (Section 3) is venue-representative, not Kraken-specific; do not reconcile a separate Kraken feed.
- Resampled from finer granularity if the venue/ccxt only returns sub-8H cleanly; otherwise
  fetched natively. Cached to parquet so runs are reproducible and offline.
- ~3 years deliberately spans bull + the 2022 bear + chop, mirroring the multi-regime
  discipline used in the FRVP V2 validation.
- **Regime tagging:** the bull/bear/chop breakdown (Section 6) uses **fixed, documented date
  ranges** declared once in config (not hand-picked per run), so the split is reproducible.
  The plan pins the exact boundaries.

## 5. Components (Python package under `MCBstrat/`)

| Module | Responsibility | Depends on |
|---|---|---|
| `data.py` | Fetch/cache Bybit BTCUSDT 8H OHLCV (ccxt or reuse cache); return a clean DataFrame | ccxt, parquet cache |
| `heikin_ashi.py` | Convert real OHLC → HA OHLC | data |
| `indicators.py` | WaveTrend (wt1/wt2), Money Flow, green/red dot detection, MF color | heikin_ashi |
| `signals.py` | Entry/exit state machine → position series | indicators |
| `backtest.py` | Event loop: signal on closed bar `t`, fill at real open `t+1`, apply costs, track equity + per-trade telemetry | signals, data |
| `metrics.py` | Aggregate stats + regime/OOS breakdowns | backtest output |
| `report.py` | Render the markdown report + equity curve | metrics |
| `tv_crosscheck.py` | TV MCP: load 8H BTC HA + VuManChu, compare Python wt1/wt2/MF-sign to TV plotted values across sample bars, report match rate | TV MCP |

Each module has one clear purpose, a typed function interface, and is testable in isolation.

**Note:** `tv_crosscheck.py` depends on a live TV MCP session and a manually-loaded VuManChu
indicator, so it is a **manual, one-time validation step** (a DoD gate, Section 10) and is NOT
wired into the automated test/run path. Mind known TV MCP symbol-drift and current-bar-only reads.

### Per-trade telemetry

Each trade records: side, entry/exit timestamps + prices, return %, **MAE %**, **MFE %**,
bars held. (No fixed stop exists, so risk is expressed in % rather than R.)

## 6. Report contents

- Equity curve (cost and zero-cost).
- Headline: CAGR, total return, win rate, profit factor, expectancy per trade, max drawdown,
  average trade, exposure %, total trade count.
- **Long-vs-short split** (does one side carry or bleed?).
- **Regime breakdown** (bull / bear / chop) — performance conditioned on market state.
- **OOS split** (in-sample first 70% / out-of-sample last 30%) — does the behavior hold out of sample?
- **TV cross-check result** — match rate between Python repro and TV's plotted indicator.

## 7. Risks & expectations (flagged upfront)

- **Sparse longs:** an oversold WaveTrend cross-up usually still has negative MF, so
  `green_dot AND MF>0` may rarely co-occur → few long trades and low statistical power.
  Trade count is reported as a headline, and a low count is called out, not hidden.
- **No stop-loss:** reversal-exit only → potentially large intra-trade MAE; reported per trade.
- **Short side in a mostly-up 3yr BTC** likely bleeds; the long/short split reveals this.
- **HA persistence:** Heikin Ashi smooths/persists signals; honest real-fill execution strips
  the HA illusion. If the edge only exists under HA-price fills, that is a finding, not a bug.

## 8. Testing (TDD)

Tests written before implementation:

- HA conversion against hand-computed values.
- WaveTrend against a reference series (and/or the TV cross-check).
- Money-flow sign correctness.
- Signal state machine (flat-between, mutual exclusion, correct flip conditions).
- Cost application (round-trip deduction).
- **No-lookahead guard (critical):** the fill price for a signal on bar `t` must come from
  bar `t+1`'s real open and must not be derivable from bar `t`'s own data.

## 9. Out of scope (YAGNI)

- Pine `strategy()` artifact.
- Live signal/alert bot.
- Parameter optimization / walk-forward grids (beyond one small documented sensitivity check).
- Multi-asset (BTC only).

## 10. Definition of done

- All tests pass, including the no-lookahead guard.
- Backtest runs end-to-end on ~3yr Bybit BTCUSDT 8H and emits the report.
- TV cross-check executed and its match rate recorded in the report.
- A clear verdict stated: edge present and cost-survivable, marginal, or absent — with the
  trade count and OOS behavior front and center.
- **Minimum-N gate:** if either side's trade count falls below a documented threshold
  (e.g. < 20 trades), the verdict for that side defaults to **"inconclusive / insufficient
  power"** rather than "absent," so a near-zero-sample result is never misread as a tested negative.
