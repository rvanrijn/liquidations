# Liquidation Cascade Probability Modeling — Agent-Based Simulation

**Date:** 2026-05-22
**Status:** Design approved, ready for implementation planning
**Scope:** Offline validation only (no live trading changes)

## Problem

The liqhunt signal engine approximates cascade likelihood with a *static* sigmoid
scorer (`src/liqhunt/quality.py`): a weighted average of normalized features
(OI depth, imbalance, funding, volatility, OI velocity, taker ratio) pushed
through a logistic. Liquidation levels themselves are estimated with a static
leverage-tier model in `src/liqlevels/client.py`
(`{100x:5%, 50x:10%, 25x:20%, 10x:35%, 5x:30%}` weights → one liquidation price
per tier).

Both representations are mechanical and first-order. Neither can express the
**reflexive feedback** that defines a real liquidation cascade: each forced
liquidation is a market order that moves price, which triggers the next tier of
liquidations, which moves price further. This feedback produces a nonlinear
tipping point — a small shock either fizzles or cascades.

This project tests one hypothesis: **does an agent-based simulation that models
the reflexive cascade carry predictive information about whether a magnet battle
resolves in favor of the bigger side (`hypothesis_correct`) beyond what the
existing snapshot features already provide?**

## Goal & Non-Goals

**Goal:** Build a fast numerical agent-based model (ABM) of liquidation cascades,
backfill its output as a `cascade_prob` column onto all 1,075 rows of
`battles_v2`, and produce a rigorous backtest report comparing it against the
current scorer at predicting `hypothesis_correct`.

**Non-goals (explicitly out of scope):**
- No live wiring into `signal_engine.py` / `quality.py`. That is a separate
  future project, decided by this project's result.
- No LLM agents. Despite the originating reference (MiroFish, an LLM-driven
  *social* simulation framework on CAMEL-AI/OASIS), liquidation is a
  deterministic arithmetic process. We borrow only the philosophy — "thousands
  of heterogeneous agents → emergent probabilistic outcome" — and implement a
  native numerical ABM. No external dependency, no API cost.
- No new live data collection. The backtest uses only features already stored in
  `battles_v2`.
- No parameter fitting to the target. Population and impact parameters are
  hand-set and physically motivated (see Overfitting Discipline).

## Prediction Target

`battles_v2.hypothesis_correct` — binary: did price move ≥ `RESOLVE_MOVE_PCT`
(0.5%) toward the bigger (magnet) side. Class balance is **548 / 527** across
1,075 rows — near-perfect, well-powered for AUC and Brier.

Sign convention (verified): `bigger_side = LONG` + `hypothesis_correct = 1`
corresponds to negative `move_pct` (price fell toward long liquidations). The
simulation shocks price toward the magnet side accordingly: down for a LONG
magnet, up for a SHORT magnet.

## Data Availability & Constraints

`battles_v2` stores only *aggregate* snapshot features per battle, not the full
per-level liquidation heatmap or order book. Available and well-populated:
`snapshot_price`, `total_long_usd` / `total_short_usd` (all 1075),
`oi_start_usd` (1056), `funding_rate` (1044), `imbalance_ratio`, `bigger_side`.

Consequences:
- The agent population is **synthesized** from these aggregates — a Monte Carlo
  over a parametric distribution, not a replay of real positions. The result
  shows whether the *mechanism* has predictive structure, not a tradeable number.
- Sanity check passed: `total_long_usd + total_short_usd ≈ oi_start_usd`.
- The `_usd` pool fields are in an ambiguous/relative unit (values ~10⁴–10⁵, not
  literal USD notional). Because every battle uses the same unit, the price-impact
  scale is **relative across battles** — fine for ranking and probability
  estimation, flagged as a caveat in the report.

**Lookahead discipline (important):** Per known project bias, `oi_change_pct` is
measured at battle *resolution*, not at entry — it is partially lookahead-
contaminated. The ABM uses **only snapshot-time data** (`snapshot_price`,
`total_long_usd`, `total_short_usd`, `oi_start_usd`, `funding_rate`,
`imbalance_ratio`, `bigger_side`) and is therefore lookahead-clean by
construction. It must never read `resolved_price`, `oi_end_usd`,
`oi_change_pct`, `mfe_pct`, `mae_pct`, `move_pct`, or any resolution-time field.

## Architecture

A new self-contained module plus an offline backtest script. Nothing touches the
live trading path.

| Unit | File | Purpose | Inputs → Output |
|------|------|---------|-----------------|
| `synthesize_population()` | `src/liqhunt/cascade_sim.py` | Build N synthetic agents from snapshot aggregates | `(price, long_usd, short_usd, oi, funding, rng)` → arrays `(side, notional, leverage, liq_price)` |
| `simulate_cascade()` | `src/liqhunt/cascade_sim.py` | One reflexive cascade run for a given shock | `(population, shock_pct, impact_params)` → `(final_move_pct, n_liquidated, settled)` |
| `cascade_probability()` | `src/liqhunt/cascade_sim.py` | Monte Carlo wrapper | `(snapshot features, n_sims, params)` → `cascade_prob ∈ [0,1]` |
| `simulate_first_order()` | `src/liqhunt/cascade_sim.py` | Ablation control — no feedback loop | same inputs → `cascade_prob_fo ∈ [0,1]` |
| backtest driver | `scripts/backtest_cascade_sim.py` | Backfill both columns onto 1075 rows, run comparison, emit report | reads `battles_v2`, writes columns + report |

Implementation should be numpy-vectorized so N ≈ 5,000 agents × n_sims × 1,075
battles × the robustness settings runs in seconds-to-minutes, fully offline.

## The Model

### Population synthesis (physically motivated, not fit to outcomes)

- Sample N agents (default 5,000). Each receives:
  - a **leverage** drawn from the existing tier weights
    `{100x:5%, 50x:10%, 25x:20%, 10x:35%, 5x:30%}`;
  - a **side** (long/short) weighted by `total_long_usd : total_short_usd`;
  - a **notional** scaled so the population aggregates reproduce the snapshot
    pools.
- **Entry prices** dispersed around `snapshot_price` within a recent-range window
  (a prior so that liquidation prices land at realistic distances per leverage
  tier).
- Each agent's **liquidation price** uses the same maintenance-margin arithmetic
  already in `client.py` (`≈ 0.9/leverage` from entry, direction per side).

### Reflexive cascade loop (the differentiator)

1. Apply an initial price shock toward the magnet side. Shock magnitude is a
   model parameter (a fixed multiple of a recent move/volatility scale derived
   from snapshot-time data) — **not** fit to the target.
2. Liquidate every agent whose `liq_price` is crossed by the current price.
3. Their combined liquidated notional becomes a forced market order. Apply price
   impact via a **square-root impact function**: `Δp = k · √(notional / depth)`.
4. Update price → re-check all agents → repeat until no new liquidations
   (cascade settles) or it runs away.

`cascade_prob` = fraction of Monte Carlo sims in which the settled move is
≥ 0.5% toward the magnet — deliberately mirroring `RESOLVE_MOVE_PCT` so the
simulated success criterion equals the real battle-resolution rule.

### First-order ablation control

`simulate_first_order()` uses the identical population but **skips the feedback**:
it counts only the agents liquidated by the direct initial shock, with no price
impact and no re-trigger, then maps that to `cascade_prob_fo` via the same 0.5%
criterion (using the shock's mechanical move, no cascade amplification). This
isolates whether *reflexivity* — not merely smoother population sampling — is the
source of any predictive edge.

**Invariant:** for any battle and seed, reflexive `cascade_prob ≥`
first-order `cascade_prob_fo` (feedback can only add liquidations, never remove
them). This is asserted in tests.

### Price-impact parameters: justification + robustness (required)

The square-root form is the empirically robust microstructure standard; the risk
is not the functional form but the parameters `k` and `depth`. The cascade
tipping point is sensitive to their product `k·depth` because of the nonlinear
feedback — a 2× error can flip a battle between "fizzles" and "cascades".
Therefore:

1. **Document** the chosen `(k, depth)` and their justification *in the report*
   (e.g., depth from a reasonable BTC-perp observation window; `k` from
   microstructure literature), not buried in code constants.
2. **Robustness sweep:** re-run the full backfill under 2–3 alternative
   `(k, depth)` settings spanning the defensible range, reported as a separate
   robustness check (not the headline). If the headline result holds across the
   range, the mechanism is real; if it is strong at one setting and worthless at
   another, the model is fitting the parameter, not capturing reflexivity. This
   is the primary defense against the "is this just curve-fit?" criticism, even
   though nothing is fit to `hypothesis_correct`.

## Data Flow

`battles_v2` row → extract snapshot-time features only → `cascade_probability()`
+ `simulate_first_order()` → write `cascade_prob` and `cascade_prob_fo` back to
`battles_v2` via additive, non-destructive `ALTER TABLE ADD COLUMN`. For the
robustness sweep, alternative-setting outputs are written to clearly-named
columns (e.g., `cascade_prob_k<setting>`) or a side table — never overwriting the
headline columns. No live code path is modified.

## Validation Methodology

All comparisons predict `hypothesis_correct`.

**Baseline:** the current sigmoid score, recomputed from battle features.
Reported twice — once as-is, once **excluding the lookahead-contaminated
`oi_change_pct`** — to give a clean apples-to-apples bar against the
lookahead-clean ABM.

**Cross-validation strategy (locked):** **time-series CV** (expanding-window or
rolling-forward folds ordered by `timestamp`), **not** random k-fold. The 1,075
battles are a regime-correlated time series; random folds leak market-regime
context (similar funding/OI/volatility) across train and test and overstate
forward-predictive performance. Time-series CV will likely report *lower* scores
than random CV — that is correct, not a problem. Additionally:
- Report the marginal `hypothesis_correct` rate **per fold** (it is not constant
  over time).
- Report **per-fold scores and their variance**, not only the mean. A single
  mean hiding high variance is withholding information we want to see.
- The CV strategy is fixed *before* running the experiment. Changing it after
  seeing results would turn an honest experiment into accidental p-hacking.

**Headline metric (pre-committed):** **incremental value** — does adding
`cascade_prob` to a logistic model that already contains the snapshot features
improve out-of-sample performance? This is the only operationally relevant
question, because in production the cascade feature would be used *alongside*,
not instead of, the existing scorer.
- **Primary decision rule: incremental Brier score** (calibration matters for
  the eventual position-sizing use case).
- **Secondary: incremental AUC** (ranking; the conventional metric).
- Both computed via time-series CV with **identical fold assignments**, plus a
  likelihood-ratio test.
- If primary and secondary agree → strong result. If they diverge → that is a
  genuinely interesting finding to discuss in the report, **not** a license to
  pick whichever looks better post-hoc.

**Standalone discrimination/calibration (secondary, advisory):** AUC and Brier /
reliability curve of `cascade_prob` alone vs. the baseline alone. Reported for
context — a feature can win standalone yet add nothing incremental, or lose
standalone yet add real complementary value.

**Ablation:** reflexive `cascade_prob` vs. first-order `cascade_prob_fo` on the
same metrics. Reflexive ≫ first-order ⇒ the cascade mechanism is the source of
edge.

**Overfitting discipline:** because population and impact priors are hand-set and
nothing is fit to `hypothesis_correct`, a positive result is overfitting-proof by
construction; the robustness sweep further guards against parameter-fitting.

## Testing

- **Population aggregates** match snapshot pools (long/short notional sums within
  tolerance of `total_long_usd` / `total_short_usd`).
- **Monotonicity:** larger shock ⇒ ≥ liquidations.
- **Reflexive ≥ first-order** always (the core invariant), across random seeds.
- **Determinism:** fixed seed ⇒ identical `cascade_prob`.
- **Degenerate sanity:** a population with no leverage clustering near price ⇒
  `cascade_prob ≈ 0`.
- **No lookahead:** assert the simulation code path never reads resolution-time
  fields.

## Risks & Honest Caveats (documented in the report)

- Synthetic population ≠ real positions; the result demonstrates whether the
  *mechanism* has predictive structure, not a directly tradeable number.
- `_usd` pool unit ambiguity ⇒ impact scale is relative across battles (fine for
  ranking, flagged).
- Tipping-point sensitivity to `k·depth` ⇒ mitigated by the robustness sweep.
- **A negative result is valid and useful.** If even the reflexive ABM does not
  beat the lookahead-clean baseline, that is a legitimate finding that the
  mechanism adds no information beyond the existing snapshot features.

## Deliverables

1. `src/liqhunt/cascade_sim.py` — the ABM module with the four units above.
2. `scripts/backtest_cascade_sim.py` — backfill + comparison driver.
3. New `battles_v2` columns: `cascade_prob`, `cascade_prob_fo` (+ robustness
   columns/side table).
4. A backtest report (markdown under `docs/`) covering: headline incremental
   Brier/AUC vs. both baselines, ablation, robustness sweep, per-fold tables,
   chosen `(k, depth)` justification, and caveats.
5. Unit tests for the invariants above.