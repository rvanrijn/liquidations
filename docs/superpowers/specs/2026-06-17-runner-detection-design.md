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

## Terminology

- **runner label** — the positive target event (the thing we are trying to predict):
  from a trigger date *T*, price rises ≥50% within 6 months (≈126 trading days)
  **without first** drawing down more than 15% from the *T* close. Forward-looking;
  used only in research.
- **(name, T) evaluation key** — every signal/tier value is computed from data at or
  before the *T* close; every runner label is computed strictly from prices after *T*.
  This key is the join used everywhere in research and is the single guard against
  time leakage.

## Architecture

New package `src/runnerhunt/` (name flexible), mirroring the existing bot module
structure. Files kept under ~500 lines, each with one clear responsibility.

```
src/runnerhunt/
  universe.py    # build liquid+growth list, point-in-time liquidity filter
  data.py        # yfinance/Stooq fetch + local parquet/sqlite cache, split-adjusted
  panel.py       # cross-sectional pass: align all names by date, compute RS rank
  signals.py     # per-name price/volume features (pure functions, unit-tested)
  classifier.py  # THE tier engine (WATCH/ACT/HOLD) — single shared source of truth
  config.py      # ClassifierConfig: frozen threshold set, persisted/loaded
  label.py       # research-only: runner label (>=50%/6mo, no >15% DD first)
  research.py    # backtest harness: signal lift, threshold calibration, walk-forward
  scanner.py     # live nightly run -> ranked tiered watchlist
  report.py      # terminal table + persisted JSON output
```

**Critical invariant — shared classifier.** Both `research.py` and `scanner.py` import
and call `classifier.py`. For the invariant to actually hold, the classifier is built
to be reproducible from a name's own history plus a frozen config:

1. **Thresholds are injected, not learned inside the classifier.** `classifier.py`
   takes a `ClassifierConfig` (frozen threshold object from `config.py`). Research
   *emits* a chosen config; live *loads* the same persisted config. See
   "Threshold calibration & config" below for which config becomes live.
2. **The classifier consumes a name's time series up to T, not an isolated row.** Tier
   assignment for (name, T) is a function of that name's bars ≤ T. Prior-tier state
   (e.g. "recently in WATCH") is therefore derivable *inside* the classifier by
   replaying the series — it is never read from an external transition log. This lets
   research replay each name's full series and reproduce live behavior exactly,
   including the WATCH→ACT transition.
3. **Cross-sectional inputs (RS rank) are precomputed in `panel.py`** and passed in as
   a per-(name, date) value, identically in research and live (see below). The
   classifier never computes cross-sectional values itself.

Research is forbidden from using any logic the live scanner cannot reproduce. This is
the anti-divergence rule.

## Components

### universe.py
- Builds the candidate list from a liquidity filter: `close > $5` and
  `50-day average dollar volume > $10M`.
- The filter is applied **point-in-time** on the price data we have at each historical
  date (so a name only enters the universe on dates it actually qualified). The **live
  universe is recomputed each nightly run** from the same filter on the latest bar, so
  a name newly crossing the thresholds enters scanning the next night.
- Honest limitation: the *ticker list* itself is sourced from today's listings, so
  delisted/failed names are largely absent. This is the survivorship-bias residual we
  cannot fully remove on free data.

### data.py
- Batch-fetches daily OHLCV via yfinance (Stooq fallback), split/dividend-adjusted.
- Caches to local parquet or sqlite; incremental updates for nightly runs.
- Validates for NaNs, gaps, and zero-volume rows.

### panel.py (cross-sectional pass)
RS rank is cross-sectional — it cannot come from one name's frame. This module runs
*before* per-name tier assignment:
- Aligns every universe name's trailing 6-month return on a common date index.
- For each date, ranks names into a percentile (RS rank). A name with a missing bar on
  a given date is **excluded from that date's ranked set** (not forward-filled),
  so percentiles reflect only names actually trading that day.
- Emits a per-(name, date) RS-rank value consumed identically by research and live.
The RS *line* (price / SPY) is per-name and lives in `signals.py`; only the *rank* is
cross-sectional.

### signals.py
Pure per-name functions over a single name's OHLCV frame (≤ T), price/volume only.
MAs are **simple** (SMA) unless a golden test shows EMA materially better:
- **Trend:** 10/20/50-day and 30-week (150d) SMAs; MA stacking (50d>150d);
  higher-lows structure.
- **Relative strength (per-name):** RS line (price / SPY). RS *rank* comes from
  `panel.py`.
- **Volatility contraction:** ATR% shrinking; successive pullback contractions (VCP).
- **Volume:** 50-day average; dry-up during base; breakout surge (≥1.5× average).
- **Position:** distance to 52-week high; base length and depth.

Benchmark for the RS line is **SPY** (cap-weighted). Noted bias: RS vs a cap-weighted
benchmark slightly favors mega-caps; revisit (equal-weight RSP) only if research shows
it suppresses real runners.

### classifier.py
Takes a name's series (≤ T) and a `ClassifierConfig`; returns a tier and a 0–100
composite score for ranking within that tier. Each tier is a boolean **gate** plus a
composite score. Prior-tier state is derived by replaying the series internally.

| Tier | Gate (thresholds from injected ClassifierConfig) |
|---|---|
| **WATCH** (pre-breakout coil) | within ~25% of 52wk high · above/flattening 30wk MA · volatility contracting · volume dried up · RS rank top ~30% · base ≥ ~5 weeks |
| **ACT** (breakout/ignition) | **prior WATCH within lookback window (required)** · close breaks base pivot · volume ≥1.5× avg · RS line at new high |
| **HOLD** (trend confirmation) | above rising 30wk MA · 50d>150d MA · RS rank top quartile · higher-lows intact |

ACT is **gated on a prior WATCH** within a configurable lookback (default ~6 weeks).
This is deliberate for precision: ACT is the *ignition of a name we were already
watching*, not any breakout. The **WATCH→ACT transition is the actionable alert.**

### config.py
`ClassifierConfig` — a frozen, serializable object holding every threshold the
classifier reads (proximity-to-high %, RS-rank cutoffs, contraction percentile, volume
multiplier, base length, ACT lookback window, etc.). Research selects one config and
persists it; live loads it. There is exactly one live config at a time.

### label.py (research only)
Computes the **runner label** for any (name, T): does price rise ≥50% within the next
6 months (≈126 trading days) **without first** drawing down more than 15% from the
*T* close. Strictly uses prices after *T*; joined to signals on the (name, T) key.
Never used by the live scanner.

### research.py
- Replays each name's full series. For every (name, T): compute per-name signals (≤ T),
  read the RS rank from `panel.py`, assign tier via `classifier.py`, and compute the
  forward runner label (> T) via `label.py`. The (name, T) key joins signal → label.
- Measure **per-signal lift**: `P(runner label = true | signal = true)` vs the base
  rate `P(runner label = true)`.
- Measure **per-tier** precision, recall, and lead-time distribution (how many trading
  days ahead of the labeled run's start does WATCH/ACT fire).

**Threshold calibration & config (resolves the live-threshold question):**
- Coarse grid search over `ClassifierConfig` thresholds, evaluated with **time-split
  walk-forward**: train/select on earlier years, score on a held-out later
  out-of-sample (OOS) period.
- Objective: **maximize precision subject to recall ≥ a floor** (provisional floor set
  against the golden tests, e.g. WATCH→ACT must fire ahead of the known winners; tuned,
  not guessed). Configs below the recall floor are rejected.
- **The config selected on the final/latest OOS fold is frozen and persisted as the
  live `ClassifierConfig`.** Live never recalibrates; it loads this one config. This is
  what makes the shared-classifier invariant concrete.
- Emit a report: signal-lift table, the chosen frozen config, and per-tier
  precision/recall/lead-time on the OOS period, with the survivorship caveat attached.

### scanner.py
- Nightly after close: recompute universe → update cache → `panel.py` RS rank →
  per-name signals → classifier (with the frozen `ClassifierConfig`) assigns tier →
  rank within tier → persist daily state.
- The classifier derives prior-tier state (WATCH→ACT transition) from each name's own
  series, so the alert is reproducible in research. The persisted daily state is for
  reporting/audit, not an input the classifier reads back.

### report.py
- Terminal table (rich, consistent with existing dashboards) and persisted JSON for
  each nightly run.

## Data Flow

**Research:** universe → fetch full history (cache) → `panel.py` cross-sectional RS rank
→ per-name signals at every (name, T) → classifier tier (replaying each name's series)
→ forward runner label (> T) → signal lift + tier precision/recall/lead-time →
walk-forward calibration → freeze + persist the live `ClassifierConfig` → report.

**Live:** nightly → recompute universe + update cache → `panel.py` RS rank on latest
date → per-name signals (≤ latest close) → classifier tier with the **loaded frozen
config** → rank within tier → persist daily state → emit WATCH→ACT alerts + ranked
watchlist.

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
