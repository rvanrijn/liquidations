# Runner Detection — Design Spec

**Date:** 2026-06-17
**Status:** Approved design, pre-implementation
**Author:** Robert van Rijn (with Claude)

## Problem

Stocks like MU (Micron) begin sustained multi-month runs (50–200%+) driven by
relative-strength leadership, volume accumulation, and base breakouts. We want a
systematic way to **spot these names early and hold the runner** — validated on
history first, then run live — applying the same signal-driven, tiered discipline
already used on the crypto book (OI/CVD rules, A+/A/B+ setup tiers).

## Goal

A tiered price/volume detector for US equities that:

1. Is **calibrated and validated on history** (research phase) before it is trusted.
2. Runs **live as a nightly scanner** that surfaces a short, ranked watchlist.
3. Reuses the **exact same tier-classification logic** in research and live, so there
   is no research/live divergence (the lookahead-bias lesson from liqhunt).

## Non-Goals

- No fundamentals (earnings, revenue, estimates) — free price/volume only.
- No ML model in v1. Rules-based composite only (Approach 1). A light interpretable
  model is a possible *future* upgrade, gated on research showing rules underperform.
- No order execution, position sizing, or broker integration. The output is a
  watchlist for the human to act on.
- No intraday data. Daily bars only.
- We do **not** claim survivorship bias is solved (see Constraints).

## Locked Decisions

| Decision | Choice |
|---|---|
| Target event | Clean multi-month runner: **≥50% within 6 months AND never draws down >15% from the trigger date first** |
| Data source | Free daily OHLCV (yfinance/Stooq), split-adjusted |
| Universe | Liquid + growth: price >$5 and 50-day avg dollar volume >$10M (~1,500–2,500 names) |
| Mode | Research/backtest first → live tiered scanner |
| Detection | Tiered state machine per name: **WATCH → ACT → HOLD** |
| Logic | Rules-based composite score (Approach 1), transparent and hand-crafted |
| Optimization target | **Precision** (false positives are the enemy), at acceptable recall |

## Architecture

New package `src/runnerhunt/` (name flexible), mirroring the existing bot module
structure. Files kept under ~500 lines, each with one clear responsibility.

```
src/runnerhunt/
  universe.py    # build liquid+growth list, point-in-time liquidity filter
  data.py        # yfinance/Stooq fetch + local parquet/sqlite cache, split-adjusted
  signals.py     # all price/volume features (pure functions, unit-tested)
  classifier.py  # THE tier engine (WATCH/ACT/HOLD) — single shared source of truth
  label.py       # research-only: B-label (>=50%/6mo, no >15% DD first)
  research.py    # backtest harness: signal lift, threshold calibration, walk-forward
  scanner.py     # live nightly run -> ranked tiered watchlist
  report.py      # terminal table + persisted JSON output
```

**Critical invariant:** `classifier.py` operates only on data available at bar-close
for a given date. Both `research.py` and `scanner.py` import and call it. Research is
forbidden from using any logic the live scanner cannot reproduce. This is the
anti-divergence rule.

## Components

### universe.py
- Builds the candidate list from a liquidity filter: `close > $5` and
  `50-day average dollar volume > $10M`.
- The filter is applied **point-in-time** on the price data we have at each historical
  date (so a name only enters the universe on dates it actually qualified).
- Honest limitation: the *ticker list* itself is sourced from today's listings, so
  delisted/failed names are largely absent. This is the survivorship-bias residual we
  cannot fully remove on free data.

### data.py
- Batch-fetches daily OHLCV via yfinance (Stooq fallback), split/dividend-adjusted.
- Caches to local parquet or sqlite; incremental updates for nightly runs.
- Validates for NaNs, gaps, and zero-volume rows.

### signals.py
Pure functions over an OHLCV frame, all derived from price/volume only:
- **Trend:** 10/20/50-day and 30-week (150d) MAs; MA stacking (50d>150d);
  higher-lows structure.
- **Relative strength:** RS line (price / SPY); RS rank (percentile of trailing
  6-month return across the universe).
- **Volatility contraction:** ATR% shrinking; successive pullback contractions (VCP).
- **Volume:** 50-day average; dry-up during base; breakout surge (≥1.5× average).
- **Position:** distance to 52-week high; base length and depth.

### classifier.py
Assigns each name a tier and a 0–100 composite score for ranking within that tier.
Each tier is a boolean **gate** plus a composite score.

| Tier | Gate (thresholds calibrated in research) |
|---|---|
| **WATCH** (pre-breakout coil) | within ~25% of 52wk high · above/flattening 30wk MA · volatility contracting · volume dried up · RS rank top ~30% · base ≥ ~5 weeks |
| **ACT** (breakout/ignition) | close breaks base pivot · volume ≥1.5× avg · RS line at new high · bonus if recently in WATCH |
| **HOLD** (trend confirmation) | above rising 30wk MA · 50d>150d MA · RS rank top quartile · higher-lows intact |

The **WATCH→ACT transition is the actionable alert.**

### label.py (research only)
Computes the B-label for any (name, date): does price rise ≥50% within the next
6 months (≈126 trading days) **without first** drawing down more than 15% from the
trigger close. Forward-looking by definition; used only to score history, never by
the live scanner.

### research.py
- For each historical date and name: compute signals, assign tier, compute the
  forward B-label.
- Measure **per-signal lift**: `P(A-move | signal=true)` vs base rate.
- Measure **per-tier** precision, recall, and lead-time distribution (how far ahead
  of the labeled run does WATCH/ACT fire).
- **Calibrate thresholds** via coarse grid search optimizing precision at acceptable
  recall, using **time-split walk-forward** (train on earlier years, validate on later)
  to limit overfitting.
- Emit a report: signal-lift table, chosen thresholds, tier precision/recall/lead-time.

### scanner.py
- Nightly after close: update cache → compute signals on the latest bar → classifier
  assigns tier → rank within tier → persist daily state.
- Tracks tier transitions across days; firing a WATCH→ACT transition is the alert.

### report.py
- Terminal table (rich, consistent with existing dashboards) and persisted JSON for
  each nightly run.

## Data Flow

**Research:** universe → fetch full history (cache) → signals at every historical date
→ classifier tier → forward B-label → signal lift + tier precision/recall/lead-time →
walk-forward threshold calibration → report.

**Live:** nightly → update cache → signals on latest bar → classifier tier → rank →
persist state → emit WATCH→ACT alerts + ranked watchlist.

## Error Handling

- Names with <~1 year history are skipped (need 52-week and base context).
- NaN/gap validation; split-adjusted closes only.
- SPY benchmark must load or RS computation aborts (RS is core, not optional).
- API failures → cached partial run + retry; a failed fetch never corrupts the cache.
- Every research report **surfaces the survivorship-bias caveat explicitly** and reads
  results conservatively. We mitigate via point-in-time price-liquidity filtering and
  time-split validation; we never claim the bias is eliminated.

## Testing

- **Unit tests per signal** on synthetic fixtures (a constructed VCP, a constructed
  breakout, a known higher-lows sequence).
- **Golden test:** feed historical data for known winners (MU, NVDA, and a few others)
  and assert WATCH→ACT fires *before* the documented run, recording the measured
  lead time.
- **Label-function tests:** synthetic price paths that do/don't satisfy the
  ≥50%/6mo + <15%-DD definition.
- **Walk-forward harness test** on a small fixture universe to confirm no time leakage.

## Risks & Open Questions

- **Survivorship bias** (residual): free ticker lists omit delisted names; backtest
  precision is optimistic. Mitigated, not solved.
- **Regime dependence:** the rules may work in trend/bull regimes and degrade in
  choppy markets; walk-forward across multiple years is the check.
- **Threshold overfitting:** mitigated by coarse grids and time-split validation, but a
  real risk on a single market's history.
- **Open:** exact pivot-detection method for base breakout (N-day high vs detected
  base high) — to be settled during implementation against the golden tests.
- **Open:** parquet vs sqlite for the cache — minor, decided at implementation time.
