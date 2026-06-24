# Liquidation Cascade ABM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fast numerical agent-based model (ABM) of reflexive liquidation cascades, backfill its `cascade_prob` output onto all 1,075 `battles_v2` rows, and produce an offline backtest report on whether it adds predictive value over the existing sigmoid scorer at predicting `hypothesis_correct`.

**Architecture:** One self-contained, numpy-vectorized module (`src/liqhunt/cascade_sim.py`) holds the model: synthesize a heterogeneous leveraged-trader population from snapshot aggregates, run a reflexive cascade (each liquidation moves price via a √-impact function, triggering the next tier), and Monte-Carlo it into a probability. A first-order (no-feedback) variant is the ablation control. An offline driver script (`scripts/backtest_cascade_sim.py`) backfills the columns and runs the time-series-CV comparison. No live trading code is touched.

**Tech Stack:** Python 3, numpy 2.4, scikit-learn 1.6 (`roc_auc_score`, `brier_score_loss`, `LogisticRegression`), scipy 1.15 (`chi2` for the likelihood-ratio test), sqlite3, pytest.

**Spec:** `docs/superpowers/specs/2026-05-22-liquidation-cascade-abm-design.md`

---

## Background the implementer needs

- **What a "battle" is:** a snapshot of the BTC liquidation imbalance. `bigger_side` = the side (LONG/SHORT) with more notional at risk. `hypothesis_correct = 1` if price subsequently moved ≥ 0.5% *toward* that bigger side (longs liquidate on the way **down**, shorts on the way **up**). The data lives in `data/magnet_battles.db`, table `battles_v2`, 1,075 rows, class balance 548/527.
- **Sign convention (verified):** `bigger_side=LONG` + correct ⇒ price fell (negative `move_pct`). So a LONG magnet ⇒ simulate a **downward** shock and track **long** liquidations; a SHORT magnet ⇒ **upward** shock, **short** liquidations.
- **Liquidation arithmetic (reuse from `src/liqlevels/client.py`):** a long at entry `E` with leverage `L` liquidates at `E·(1 − 0.9/L)`; a short at `E·(1 + 0.9/L)`. The `0.9` is the maintenance-margin haircut already used in the codebase.
- **Leverage tier weights (reuse from `client.py`):** `{100x:0.05, 50x:0.10, 25x:0.20, 10x:0.35, 5x:0.30}`.
- **Lookahead rule (critical):** the simulation must read **only** snapshot-time fields: `snapshot_price`, `total_long_usd`, `total_short_usd`, `oi_start_usd`, `funding_rate`, `imbalance_ratio`, `bigger_side`. It must **never** read `resolved_price`, `oi_end_usd`, `oi_change_pct`, `mfe_pct`, `mae_pct`, `move_pct`, `oi_min_pct`, `oi_max_pct`, `funding_rate_end`, `duration_seconds`. This is enforced structurally: the sim functions take a typed `Snapshot`/feature args, never a raw battle row.
- **Unit caveat:** the `_usd` pools are NOT literal USD and the unit is *inconsistent across the dataset's collection eras* (values span ~10⁴ on some rows to ~10⁹ on others). This is fine — and actually appropriate — because the √-impact term uses the ratio `notional/depth` (both derived from the same per-row pool), so absolute scale cancels per battle. cascade_prob therefore depends on the *relative* leverage/imbalance structure, not a meaningless absolute notional. Document chosen `(k, depth)` and this cancellation in the report.

## File structure

| File | Responsibility | Create/Modify |
|------|----------------|---------------|
| `src/liqhunt/cascade_sim.py` | The ABM: `CascadeParams`, `synthesize_population`, `simulate_cascade`, `simulate_first_order`, `cascade_probability`, `first_order_probability` | Create |
| `tests/liqhunt/test_cascade_sim.py` | Unit tests for all invariants | Create |
| `scripts/backtest_cascade_sim.py` | Offline driver: backfill columns, baseline reconstruction, time-series-CV comparison, ablation, robustness sweep, report | Create |
| `docs/cascade-sim-backtest-report.md` | Generated report (written by the script; committed once) | Create (by script) |

No existing files are modified. `src/liqhunt/quality.py` and `src/liqlevels/client.py` are read for constants only.

---

## Task 1: Module skeleton + `CascadeParams`

**Files:**
- Create: `src/liqhunt/cascade_sim.py`
- Test: `tests/liqhunt/test_cascade_sim.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/liqhunt/test_cascade_sim.py
"""Tests for the liquidation cascade agent-based model."""

import numpy as np

from src.liqhunt.cascade_sim import CascadeParams


def test_cascade_params_defaults():
    """CascadeParams exposes documented, physically-motivated defaults."""
    p = CascadeParams()
    # Population
    assert p.n_agents == 5000
    assert p.leverage_weights == {100: 0.05, 50: 0.10, 25: 0.20, 10: 0.35, 5: 0.30}
    assert p.maint_haircut == 0.9
    assert p.entry_window_pct == 0.015      # ±1.5% entry dispersion around snapshot
    # Shock (drawn per-sim, NOT fit to target)
    assert p.shock_center_pct == 0.003      # 0.3% initial nudge toward magnet
    assert p.shock_std_pct == 0.0015
    # Price impact: dp = k * sqrt(notional / depth)
    assert p.impact_k == 0.4
    assert p.depth_frac_of_oi == 0.5        # depth = 0.5 * oi_start_usd
    # Monte Carlo + success criterion
    assert p.n_sims == 200
    assert p.success_move_pct == 0.005       # mirrors RESOLVE_MOVE_PCT (0.5%)
    assert p.max_cascade_steps == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py::test_cascade_params_defaults -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.liqhunt.cascade_sim'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/liqhunt/cascade_sim.py
"""Agent-based model of reflexive BTC liquidation cascades.

Offline / research use only — NOT wired into live trading.

Given a snapshot of the liquidation imbalance (price + long/short pools + OI),
synthesize a heterogeneous population of leveraged traders, apply a price shock
toward the magnet side, and let liquidations reflexively push price further
(each forced close is a market order). Monte-Carlo this into `cascade_prob`:
the fraction of sims where price settles >= success_move_pct toward the magnet.

A first-order variant (no price-impact feedback) is the ablation control.

LOOKAHEAD DISCIPLINE: every public function takes only snapshot-time inputs.
No resolution-time field (resolved_price, move_pct, oi_change_pct, mfe/mae, ...)
is ever an argument. Do not add one.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _default_leverage_weights() -> dict[int, float]:
    # Mirrors src/liqlevels/client.py leverage_weights.
    return {100: 0.05, 50: 0.10, 25: 0.20, 10: 0.35, 5: 0.30}


@dataclass(frozen=True)
class CascadeParams:
    """Physically-motivated parameters. NONE are fit to hypothesis_correct.

    Defaults are documented in docs/cascade-sim-backtest-report.md and are
    stress-tested by the robustness sweep in scripts/backtest_cascade_sim.py.
    """

    # Population
    n_agents: int = 5000
    leverage_weights: dict[int, float] = field(default_factory=_default_leverage_weights)
    maint_haircut: float = 0.9          # liq at entry*(1 -/+ haircut/leverage)
    entry_window_pct: float = 0.015     # uniform ±window around snapshot_price

    # Shock — drawn per Monte Carlo sim (gives cascade_prob_fo real spread)
    shock_center_pct: float = 0.003
    shock_std_pct: float = 0.0015

    # Price impact: dp_fraction = impact_k * sqrt(liquidated_notional / depth)
    impact_k: float = 0.4
    depth_frac_of_oi: float = 0.5       # depth = depth_frac_of_oi * oi_start_usd

    # Monte Carlo + success criterion
    n_sims: int = 200
    success_move_pct: float = 0.005     # mirrors RESOLVE_MOVE_PCT
    max_cascade_steps: int = 50         # safety cap on the feedback loop
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py::test_cascade_params_defaults -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/liqhunt/cascade_sim.py tests/liqhunt/test_cascade_sim.py
git commit -m "feat(cascade-sim): module skeleton + CascadeParams"
```

---

## Task 2: `synthesize_population`

Builds N agents from snapshot aggregates. Returns numpy arrays. The long/short
notional split matches the pools; leverage is sampled from tier weights; entry
prices are dispersed uniformly within ±`entry_window_pct` of `snapshot_price`;
liquidation price uses the maintenance-margin formula.

**Files:**
- Modify: `src/liqhunt/cascade_sim.py`
- Test: `tests/liqhunt/test_cascade_sim.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/liqhunt/test_cascade_sim.py
from src.liqhunt.cascade_sim import synthesize_population


def _pop(rng, **over):
    kw = dict(
        snapshot_price=100_000.0,
        total_long_usd=60_000.0,
        total_short_usd=40_000.0,
        oi_start_usd=100_000.0,
        params=CascadeParams(n_agents=4000),
        rng=rng,
    )
    kw.update(over)
    return synthesize_population(**kw)


def test_population_size_and_fields():
    pop = _pop(np.random.default_rng(0))
    # side: +1 long, -1 short ; plus notional, leverage, entry, liq_price arrays
    assert pop.side.shape == (4000,)
    assert pop.notional.shape == (4000,)
    assert pop.leverage.shape == (4000,)
    assert pop.liq_price.shape == (4000,)
    assert set(np.unique(pop.side)).issubset({-1, 1})


def test_population_notional_matches_pools():
    """Aggregate long/short notional reproduces the snapshot pools (within 2%)."""
    pop = _pop(np.random.default_rng(1))
    long_notional = pop.notional[pop.side == 1].sum()
    short_notional = pop.notional[pop.side == -1].sum()
    assert abs(long_notional - 60_000.0) / 60_000.0 < 0.02
    assert abs(short_notional - 40_000.0) / 40_000.0 < 0.02


def test_population_liq_prices_directional():
    """Long liq prices are below entry; short liq prices above entry."""
    pop = _pop(np.random.default_rng(2))
    longs = pop.side == 1
    shorts = pop.side == -1
    assert np.all(pop.liq_price[longs] < pop.entry_price[longs])
    assert np.all(pop.liq_price[shorts] > pop.entry_price[shorts])


def test_population_deterministic_under_seed():
    a = _pop(np.random.default_rng(7))
    b = _pop(np.random.default_rng(7))
    assert np.array_equal(a.liq_price, b.liq_price)
    assert np.array_equal(a.notional, b.notional)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py -k population -v`
Expected: FAIL — `ImportError: cannot import name 'synthesize_population'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add imports at top of src/liqhunt/cascade_sim.py
import numpy as np
```

```python
# append to src/liqhunt/cascade_sim.py

@dataclass(frozen=True)
class Population:
    """A synthesized agent population. All arrays are length n_agents."""
    side: np.ndarray        # +1 long, -1 short
    notional: np.ndarray    # per-agent notional in pool units
    leverage: np.ndarray    # int leverage
    entry_price: np.ndarray
    liq_price: np.ndarray


def synthesize_population(
    *,
    snapshot_price: float,
    total_long_usd: float,
    total_short_usd: float,
    oi_start_usd: float,
    params: CascadeParams,
    rng: np.random.Generator,
) -> Population:
    """Build an agent population from snapshot-time aggregates only.

    Notional is split long/short to match the pools; leverage sampled from tier
    weights; entry dispersed uniformly within +/- entry_window_pct of price;
    liq price from the maintenance-margin formula.
    """
    n = params.n_agents
    levs = np.array(list(params.leverage_weights.keys()))
    probs = np.array(list(params.leverage_weights.values()), dtype=float)
    probs = probs / probs.sum()

    # Side assignment proportional to pools.
    total_pool = total_long_usd + total_short_usd
    p_long = (total_long_usd / total_pool) if total_pool > 0 else 0.5
    is_long = rng.random(n) < p_long
    side = np.where(is_long, 1, -1).astype(np.int8)

    leverage = rng.choice(levs, size=n, p=probs)

    # Equal-weight notional within each side, scaled so sums match the pools.
    notional = np.empty(n, dtype=float)
    n_long = int(is_long.sum())
    n_short = n - n_long
    if n_long > 0:
        notional[is_long] = total_long_usd / n_long
    if n_short > 0:
        notional[~is_long] = total_short_usd / n_short

    # Entry dispersion: uniform within +/- entry_window_pct of snapshot price.
    w = params.entry_window_pct
    entry_price = snapshot_price * (1.0 + rng.uniform(-w, w, size=n))

    # Liquidation price.
    haircut = params.maint_haircut
    liq_price = np.where(
        side == 1,
        entry_price * (1.0 - haircut / leverage),
        entry_price * (1.0 + haircut / leverage),
    )

    return Population(
        side=side, notional=notional, leverage=leverage.astype(int),
        entry_price=entry_price, liq_price=liq_price,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py -k population -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/liqhunt/cascade_sim.py tests/liqhunt/test_cascade_sim.py
git commit -m "feat(cascade-sim): synthesize_population from snapshot aggregates"
```

---

## Task 3: `simulate_cascade` (reflexive single run)

One cascade run for a given magnet side + shock. Returns the settled signed move
fraction and how many agents liquidated. Down-cascade (LONG magnet) liquidates
longs as price falls; up-cascade (SHORT magnet) liquidates shorts as price rises.
Each step liquidates all newly-crossed agents on the magnet side, converts their
notional to a price move via the √-impact function, and repeats until no new
liquidations or `max_cascade_steps`.

**Files:**
- Modify: `src/liqhunt/cascade_sim.py`
- Test: `tests/liqhunt/test_cascade_sim.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/liqhunt/test_cascade_sim.py
from src.liqhunt.cascade_sim import simulate_cascade


def test_cascade_monotonic_in_shock():
    """A larger initial shock liquidates >= as many agents."""
    rng = np.random.default_rng(3)
    pop = _pop(rng, total_long_usd=60_000.0, total_short_usd=40_000.0)
    p = CascadeParams()
    small = simulate_cascade(pop, magnet_side="LONG", shock_pct=0.002,
                             depth=50_000.0, params=p)
    big = simulate_cascade(pop, magnet_side="LONG", shock_pct=0.02,
                           depth=50_000.0, params=p)
    assert big.n_liquidated >= small.n_liquidated
    assert abs(big.move_pct) >= abs(small.move_pct)


def test_cascade_direction_signs():
    """LONG magnet => downward (negative) move; SHORT magnet => positive."""
    rng = np.random.default_rng(4)
    pop = _pop(rng)
    p = CascadeParams()
    down = simulate_cascade(pop, magnet_side="LONG", shock_pct=0.01,
                            depth=50_000.0, params=p)
    up = simulate_cascade(pop, magnet_side="SHORT", shock_pct=0.01,
                          depth=50_000.0, params=p)
    assert down.move_pct <= 0.0
    assert up.move_pct >= 0.0


def test_cascade_settles_within_step_cap():
    rng = np.random.default_rng(5)
    pop = _pop(rng)
    p = CascadeParams(max_cascade_steps=10)
    res = simulate_cascade(pop, magnet_side="LONG", shock_pct=0.05,
                           depth=10_000.0, params=p)
    assert res.steps <= 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py -k cascade -v`
Expected: FAIL — `cannot import name 'simulate_cascade'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/liqhunt/cascade_sim.py

@dataclass(frozen=True)
class CascadeResult:
    move_pct: float       # signed settled move (negative = down)
    n_liquidated: int
    steps: int


def simulate_cascade(
    population: Population,
    *,
    magnet_side: str,
    shock_pct: float,
    depth: float,
    params: CascadeParams,
) -> CascadeResult:
    """Run one reflexive cascade. shock_pct is the magnitude of the initial nudge
    toward the magnet (positive number); direction is set by magnet_side.

    Down-cascade (LONG): price falls, longs with liq_price >= price liquidate,
    each forced SELL pushes price further down. Symmetric for SHORT.
    """
    down = magnet_side == "LONG"
    mag_mask = (population.side == 1) if down else (population.side == -1)
    liq_price = population.liq_price
    notional = population.notional

    start_price = float(population.entry_price.mean())  # reference for move_pct
    # Begin from snapshot price proxy: use the dispersion center.
    # entry_price mean ~ snapshot_price by construction; use it as price origin.
    price = start_price * (1.0 - shock_pct) if down else start_price * (1.0 + shock_pct)

    liquidated = np.zeros(population.side.shape[0], dtype=bool)
    steps = 0
    for _ in range(params.max_cascade_steps):
        steps += 1
        if down:
            newly = mag_mask & (~liquidated) & (liq_price >= price)
        else:
            newly = mag_mask & (~liquidated) & (liq_price <= price)
        if not newly.any():
            break
        liquidated |= newly
        batch_notional = float(notional[newly].sum())
        dp = params.impact_k * np.sqrt(max(batch_notional, 0.0) / depth)
        price = price * (1.0 - dp) if down else price * (1.0 + dp)

    move_pct = (price - start_price) / start_price
    return CascadeResult(
        move_pct=float(move_pct),
        n_liquidated=int(liquidated.sum()),
        steps=steps,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py -k cascade -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/liqhunt/cascade_sim.py tests/liqhunt/test_cascade_sim.py
git commit -m "feat(cascade-sim): reflexive simulate_cascade single run"
```

---

## Task 4: `simulate_first_order` (ablation control)

Identical population + shock, but **no feedback**: only agents liquidated by the
direct initial shock count; no price impact, no re-trigger. Used to isolate
whether reflexivity (not just sampling) carries the edge.

**Files:**
- Modify: `src/liqhunt/cascade_sim.py`
- Test: `tests/liqhunt/test_cascade_sim.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/liqhunt/test_cascade_sim.py
from src.liqhunt.cascade_sim import simulate_first_order


def test_first_order_le_reflexive_invariant():
    """Reflexive cascade liquidates >= first-order for the same pop+shock+seed."""
    rng = np.random.default_rng(11)
    pop = _pop(rng)
    p = CascadeParams()
    for shock in (0.002, 0.005, 0.01, 0.03):
        fo = simulate_first_order(pop, magnet_side="LONG", shock_pct=shock, params=p)
        rx = simulate_cascade(pop, magnet_side="LONG", shock_pct=shock,
                              depth=50_000.0, params=p)
        assert rx.n_liquidated >= fo.n_liquidated, shock
        assert abs(rx.move_pct) >= abs(fo.move_pct) - 1e-12, shock


def test_first_order_no_feedback_move_equals_shock():
    """First-order move equals the raw shock (no amplification)."""
    rng = np.random.default_rng(12)
    pop = _pop(rng)
    p = CascadeParams()
    fo = simulate_first_order(pop, magnet_side="LONG", shock_pct=0.004, params=p)
    assert abs(fo.move_pct - (-0.004)) < 1e-12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py -k first_order -v`
Expected: FAIL — `cannot import name 'simulate_first_order'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/liqhunt/cascade_sim.py

def simulate_first_order(
    population: Population,
    *,
    magnet_side: str,
    shock_pct: float,
    params: CascadeParams,
) -> CascadeResult:
    """No-feedback ablation: count only agents the raw shock directly liquidates.

    The settled move is exactly the shock (no impact amplification), so this
    isolates the population-sampling contribution from the reflexive mechanism.
    """
    down = magnet_side == "LONG"
    mag_mask = (population.side == 1) if down else (population.side == -1)
    start_price = float(population.entry_price.mean())
    price = start_price * (1.0 - shock_pct) if down else start_price * (1.0 + shock_pct)

    if down:
        liquidated = mag_mask & (population.liq_price >= price)
    else:
        liquidated = mag_mask & (population.liq_price <= price)

    move_pct = -shock_pct if down else shock_pct
    return CascadeResult(
        move_pct=float(move_pct),
        n_liquidated=int(liquidated.sum()),
        steps=1,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py -k first_order -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/liqhunt/cascade_sim.py tests/liqhunt/test_cascade_sim.py
git commit -m "feat(cascade-sim): first-order ablation + reflexive>=first-order invariant"
```

---

## Task 5: Monte Carlo wrappers `cascade_probability` / `first_order_probability`

Run `n_sims` with per-sim random population **and** per-sim random shock (drawn
from a truncated-normal around `shock_center_pct`). `cascade_prob` = fraction of
sims whose settled move ≥ `success_move_pct` toward the magnet. `depth` is
derived per battle as `depth_frac_of_oi * oi_start_usd`.

**Files:**
- Modify: `src/liqhunt/cascade_sim.py`
- Test: `tests/liqhunt/test_cascade_sim.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/liqhunt/test_cascade_sim.py
from src.liqhunt.cascade_sim import cascade_probability, first_order_probability


def _features(**over):
    f = dict(
        snapshot_price=100_000.0,
        total_long_usd=60_000.0,
        total_short_usd=40_000.0,
        oi_start_usd=100_000.0,
        bigger_side="LONG",
    )
    f.update(over)
    return f


def test_cascade_prob_in_unit_interval():
    p = CascadeParams(n_sims=50)
    prob = cascade_probability(**_features(), params=p, seed=0)
    assert 0.0 <= prob <= 1.0


def test_cascade_prob_deterministic_under_seed():
    p = CascadeParams(n_sims=50)
    a = cascade_probability(**_features(), params=p, seed=42)
    b = cascade_probability(**_features(), params=p, seed=42)
    assert a == b


def test_cascade_prob_ge_first_order_prob():
    """Reflexive prob >= first-order prob for the same battle+seed (invariant)."""
    p = CascadeParams(n_sims=100)
    feats = _features()
    rx = cascade_probability(**feats, params=p, seed=5)
    fo = first_order_probability(**feats, params=p, seed=5)
    assert rx >= fo - 1e-9


def test_cascade_prob_degenerate_thin_pool():
    """A near-empty magnet pool (almost no agents to liquidate) => prob ~ 0."""
    p = CascadeParams(n_sims=100)
    prob = cascade_probability(
        **_features(total_long_usd=1.0, total_short_usd=99_000.0, oi_start_usd=99_001.0),
        params=p, seed=1,
    )
    assert prob < 0.05


def test_cascade_prob_rises_with_bigger_pool():
    """Denser magnet pool (more leveraged longs) => higher cascade prob."""
    p = CascadeParams(n_sims=150)
    thin = cascade_probability(
        **_features(total_long_usd=30_000.0, total_short_usd=30_000.0, oi_start_usd=60_000.0),
        params=p, seed=2)
    thick = cascade_probability(
        **_features(total_long_usd=120_000.0, total_short_usd=30_000.0, oi_start_usd=150_000.0),
        params=p, seed=2)
    assert thick >= thin
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py -k "prob" -v`
Expected: FAIL — `cannot import name 'cascade_probability'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/liqhunt/cascade_sim.py

def _draw_shock(rng: np.random.Generator, params: CascadeParams) -> float:
    """Truncated-normal shock magnitude, floored at a small positive value."""
    s = rng.normal(params.shock_center_pct, params.shock_std_pct)
    return float(max(s, 1e-4))


def _monte_carlo(
    *, snapshot_price, total_long_usd, total_short_usd, oi_start_usd,
    bigger_side, params, seed, reflexive,
) -> float:
    rng = np.random.default_rng(seed)
    depth = max(params.depth_frac_of_oi * oi_start_usd, 1e-9)
    successes = 0
    threshold = params.success_move_pct
    for _ in range(params.n_sims):
        pop = synthesize_population(
            snapshot_price=snapshot_price,
            total_long_usd=total_long_usd,
            total_short_usd=total_short_usd,
            oi_start_usd=oi_start_usd,
            params=params, rng=rng,
        )
        shock = _draw_shock(rng, params)
        if reflexive:
            res = simulate_cascade(pop, magnet_side=bigger_side,
                                   shock_pct=shock, depth=depth, params=params)
        else:
            res = simulate_first_order(pop, magnet_side=bigger_side,
                                       shock_pct=shock, params=params)
        if abs(res.move_pct) >= threshold:
            successes += 1
    return successes / params.n_sims


def cascade_probability(
    *, snapshot_price, total_long_usd, total_short_usd, oi_start_usd,
    bigger_side, params: CascadeParams, seed: int,
) -> float:
    """Reflexive Monte Carlo cascade probability in [0, 1]."""
    return _monte_carlo(
        snapshot_price=snapshot_price, total_long_usd=total_long_usd,
        total_short_usd=total_short_usd, oi_start_usd=oi_start_usd,
        bigger_side=bigger_side, params=params, seed=seed, reflexive=True,
    )


def first_order_probability(
    *, snapshot_price, total_long_usd, total_short_usd, oi_start_usd,
    bigger_side, params: CascadeParams, seed: int,
) -> float:
    """First-order (no-feedback) Monte Carlo probability in [0, 1]."""
    return _monte_carlo(
        snapshot_price=snapshot_price, total_long_usd=total_long_usd,
        total_short_usd=total_short_usd, oi_start_usd=oi_start_usd,
        bigger_side=bigger_side, params=params, seed=seed, reflexive=False,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py -k "prob" -v`
Expected: PASS (5 tests). Then run the full module suite:
Run: `python -m pytest tests/liqhunt/test_cascade_sim.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/liqhunt/cascade_sim.py tests/liqhunt/test_cascade_sim.py
git commit -m "feat(cascade-sim): Monte Carlo cascade_probability + first_order_probability"
```

---

## Task 6: Backtest driver — DB load + column backfill

The script reads `battles_v2`, computes `cascade_prob` + `cascade_prob_fo` for
every row (snapshot-time fields only), and writes them to two new columns via
non-destructive `ALTER TABLE ADD COLUMN`. Idempotent: re-running recomputes and
overwrites the values, not the schema.

**Files:**
- Create: `scripts/backtest_cascade_sim.py`

- [ ] **Step 1: Write the loader + backfill (no test harness; verified by running)**

```python
# scripts/backtest_cascade_sim.py
"""Offline backtest: does the cascade ABM add predictive value over the
existing sigmoid scorer at predicting battles_v2.hypothesis_correct?

Run:  python -m scripts.backtest_cascade_sim
Outputs: backfilled columns on battles_v2 + docs/cascade-sim-backtest-report.md

NOTHING here is wired into live trading.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from src.liqhunt.cascade_sim import (
    CascadeParams, cascade_probability, first_order_probability,
)

DB_PATH = Path("data/magnet_battles.db")
REPORT_PATH = Path("docs/cascade-sim-backtest-report.md")

# Snapshot-time fields ONLY. No resolution-time leakage.
SNAPSHOT_COLS = (
    "id, bigger_side, snapshot_price, total_long_usd, total_short_usd, "
    "oi_start_usd, funding_rate, imbalance_ratio, hypothesis_correct, timestamp"
)


def load_battles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT {SNAPSHOT_COLS} FROM battles_v2 ORDER BY timestamp ASC"
    ).fetchall()
    return rows


def ensure_columns(conn: sqlite3.Connection, columns: list[str]) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(battles_v2)")}
    for col in columns:
        if col not in existing:
            conn.execute(f"ALTER TABLE battles_v2 ADD COLUMN {col} REAL DEFAULT NULL")
    conn.commit()


def backfill(conn: sqlite3.Connection, params: CascadeParams,
             rx_col: str, fo_col: str, base_seed: int = 1234) -> None:
    rows = load_battles(conn)
    ensure_columns(conn, [rx_col, fo_col])
    for row in rows:
        # Deterministic per-row seed => reproducible backfill.
        seed = base_seed + int(row["id"])
        feats = dict(
            snapshot_price=row["snapshot_price"],
            total_long_usd=row["total_long_usd"],
            total_short_usd=row["total_short_usd"],
            oi_start_usd=row["oi_start_usd"] or (
                row["total_long_usd"] + row["total_short_usd"]),
            bigger_side=row["bigger_side"],
        )
        rx = cascade_probability(**feats, params=params, seed=seed)
        fo = first_order_probability(**feats, params=params, seed=seed)
        conn.execute(
            f"UPDATE battles_v2 SET {rx_col}=?, {fo_col}=? WHERE id=?",
            (rx, fo, row["id"]),
        )
    conn.commit()


if __name__ == "__main__":
    if not DB_PATH.exists():
        raise SystemExit(f"ERROR: {DB_PATH} not found")
    conn = sqlite3.connect(DB_PATH)
    params = CascadeParams()
    print("Backfilling cascade_prob + cascade_prob_fo ...")
    backfill(conn, params, "cascade_prob", "cascade_prob_fo")
    n = conn.execute(
        "SELECT COUNT(*) FROM battles_v2 WHERE cascade_prob IS NOT NULL"
    ).fetchone()[0]
    print(f"  backfilled {n} rows")
    conn.close()
```

- [ ] **Step 2: Run the backfill to verify it works**

Run: `python -m scripts.backtest_cascade_sim`
Expected: prints `backfilled 1075 rows`. (Takes up to a few minutes for 1075 × 200 sims × 2.)

- [ ] **Step 3: Verify columns exist and are populated**

Run:
```bash
sqlite3 data/magnet_battles.db "SELECT COUNT(*), MIN(cascade_prob), AVG(cascade_prob), MAX(cascade_prob), AVG(cascade_prob_fo) FROM battles_v2 WHERE cascade_prob IS NOT NULL;"
```
Expected: count 1075, values within [0,1], and `AVG(cascade_prob) >= AVG(cascade_prob_fo)` (reflexive ≥ first-order on average).

- [ ] **Step 4: Commit**

```bash
git add scripts/backtest_cascade_sim.py
git commit -m "feat(cascade-sim): backtest driver loads battles_v2 + backfills cascade columns"
```

> Note: the `.db` file is data, not code. Do not commit `data/magnet_battles.db`; confirm it is git-ignored (`git status --porcelain data/magnet_battles.db` shows nothing).

---

## Task 7: Baseline reconstruction (lookahead-clean sigmoid)

Reconstruct the existing sigmoid quality score from the battle features, dropping
the three live-only features absent from `battles_v2` (renormalizing
`WEIGHT_SUM`). Produce two baseline scores per row: `baseline_full` (includes
`oi_change_pct`) and `baseline_clean` (excludes the lookahead-contaminated
`oi_change_pct`). These are computed in-memory for scoring — NOT written to the
DB. `oi_change_pct` is read here **only** to build the deliberately-contaminated
`baseline_full` comparison bar; the ABM never sees it.

**Files:**
- Modify: `scripts/backtest_cascade_sim.py`

- [ ] **Step 1: Add the baseline functions**

```python
# add near the top imports
from src.liqhunt.quality import (
    W_OI, W_IMB, W_FUND, sigmoid_gate,
)
```

```python
# append to scripts/backtest_cascade_sim.py

def _norm_oi(oi_change_pct: float) -> float:
    return min(abs(oi_change_pct) / 1.0, 1.0)


def _norm_imb(imbalance_ratio: float) -> float:
    return max(min((imbalance_ratio - 1.0) / 2.0, 1.0), 0.0)


def _norm_fund(funding_rate: float, magnet_side: str) -> float:
    if magnet_side == "LONG":
        return min(max(funding_rate / 0.001, 0.0), 1.0)
    return min(max(-funding_rate / 0.001, 0.0), 1.0)


def baseline_scores(row) -> tuple[float, float]:
    """Reconstruct the sigmoid baseline from battle features only.

    Drops live-only features (price_range_6h, oi_velocity, taker_ratio) and
    renormalizes over the present weights. Returns (full, clean) where `clean`
    excludes the lookahead-contaminated oi_change_pct (W_OI).
    """
    nimb = _norm_imb(row["imbalance_ratio"])
    nfund = _norm_fund(row["funding_rate"] or 0.0, row["bigger_side"])

    # full: includes OI (note oi_change_pct is resolution-measured = lookahead)
    noi = _norm_oi(row["oi_change_pct"] if "oi_change_pct" in row.keys() else 0.0)
    wsum_full = W_OI + W_IMB + W_FUND
    q_full = (W_OI * noi + W_IMB * nimb + W_FUND * nfund) / wsum_full if wsum_full else 0.0

    # clean: imbalance + funding only (lookahead-free)
    wsum_clean = W_IMB + W_FUND
    q_clean = (W_IMB * nimb + W_FUND * nfund) / wsum_clean if wsum_clean else 0.0

    return sigmoid_gate(q_full), sigmoid_gate(q_clean)
```

> The loader query in Task 6 selects `funding_rate` and `imbalance_ratio`. Add `oi_change_pct` to `SNAPSHOT_COLS` **only** for building `baseline_full` (it is never passed to the ABM). Update the `SELECT` list accordingly and add a code comment marking it as baseline-only / not-for-sim.

- [ ] **Step 2: Quick sanity check in a REPL-style run**

Run:
```bash
python -c "
import sqlite3; from scripts.backtest_cascade_sim import baseline_scores
c=sqlite3.connect('data/magnet_battles.db'); c.row_factory=sqlite3.Row
r=c.execute('SELECT * FROM battles_v2 LIMIT 1').fetchone()
print(baseline_scores(r))"
```
Expected: prints two floats in (0,1).

- [ ] **Step 3: Commit**

```bash
git add scripts/backtest_cascade_sim.py
git commit -m "feat(cascade-sim): reconstruct lookahead-clean sigmoid baseline"
```

---

## Task 8: Time-series CV comparison (headline: incremental Brier, secondary AUC)

Pre-committed methodology: **time-series CV** (expanding-window folds ordered by
`timestamp`), headline = **incremental Brier**, secondary = incremental AUC, plus
a likelihood-ratio test. "Incremental" = does adding `cascade_prob` to a logistic
model already containing the baseline snapshot features improve out-of-sample
performance.

**Files:**
- Modify: `scripts/backtest_cascade_sim.py`

- [ ] **Step 1: Add CV utilities + the incremental comparison**

```python
# add imports
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, brier_score_loss
from scipy.stats import chi2
```

```python
# append to scripts/backtest_cascade_sim.py

def expanding_folds(n: int, n_folds: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window time-series folds over rows already sorted by timestamp.

    Fold k trains on [0 .. split_k) and tests on [split_k .. split_{k+1}).
    No future row ever appears in a training set for a past test row.
    """
    bounds = np.linspace(0, n, n_folds + 2, dtype=int)
    folds = []
    for k in range(1, n_folds + 1):
        train_idx = np.arange(0, bounds[k])
        test_idx = np.arange(bounds[k], bounds[k + 1])
        if len(test_idx) > 0 and len(train_idx) > 0:
            folds.append((train_idx, test_idx))
    return folds


def _fit_predict(X_tr, y_tr, X_te):
    model = LogisticRegression(max_iter=1000)
    model.fit(X_tr, y_tr)
    return model.predict_proba(X_te)[:, 1], model


def incremental_cv(X_base, X_aug, y, folds) -> dict:
    """Compare base-feature model vs base+extra feature model, out-of-sample.

    X_base: baseline feature matrix (n, b)
    X_aug:  X_base with the extra column(s) appended (n, b+e)
    Returns per-fold + aggregate Brier/AUC for both, plus LR-test p-value.
    """
    rows = []
    ll_base_total = ll_aug_total = 0.0
    for train_idx, test_idx in folds:
        y_te = y[test_idx]
        pb, _ = _fit_predict(X_base[train_idx], y[train_idx], X_base[test_idx])
        pa, _ = _fit_predict(X_aug[train_idx], y[train_idx], X_aug[test_idx])
        eps = 1e-12
        pb = np.clip(pb, eps, 1 - eps)
        pa = np.clip(pa, eps, 1 - eps)
        # AUC only defined if both classes present in the test fold.
        auc_b = roc_auc_score(y_te, pb) if len(np.unique(y_te)) == 2 else float("nan")
        auc_a = roc_auc_score(y_te, pa) if len(np.unique(y_te)) == 2 else float("nan")
        rows.append({
            "n_test": int(len(test_idx)),
            "pos_rate": float(y_te.mean()),
            "brier_base": float(brier_score_loss(y_te, pb)),
            "brier_aug": float(brier_score_loss(y_te, pa)),
            "auc_base": auc_b,
            "auc_aug": auc_a,
        })
        ll_base_total += np.sum(y_te * np.log(pb) + (1 - y_te) * np.log(1 - pb))
        ll_aug_total += np.sum(y_te * np.log(pa) + (1 - y_te) * np.log(1 - pa))

    extra_df = X_aug.shape[1] - X_base.shape[1]
    lr_stat = 2.0 * (ll_aug_total - ll_base_total)
    lr_p = float(chi2.sf(lr_stat, df=max(extra_df, 1))) if lr_stat > 0 else 1.0

    def _mean(key):
        vals = [r[key] for r in rows if not np.isnan(r[key])]
        return float(np.mean(vals)) if vals else float("nan")

    def _std(key):
        vals = [r[key] for r in rows if not np.isnan(r[key])]
        return float(np.std(vals)) if vals else float("nan")

    return {
        "folds": rows,
        "brier_base_mean": _mean("brier_base"), "brier_aug_mean": _mean("brier_aug"),
        "brier_delta": _mean("brier_base") - _mean("brier_aug"),  # >0 = improvement
        "auc_base_mean": _mean("auc_base"), "auc_aug_mean": _mean("auc_aug"),
        "auc_delta": _mean("auc_aug") - _mean("auc_base"),        # >0 = improvement
        "auc_aug_std": _std("auc_aug"),
        "lr_stat": float(lr_stat), "lr_p": lr_p, "extra_df": int(extra_df),
    }
```

- [ ] **Step 2: Verify on the real data via a temporary print**

Add a temporary `if __name__` branch (or run inline) that loads rows, builds:
- `y` = `hypothesis_correct`
- baseline matrix from `[baseline_clean]` (and a variant with `[norm_imb, norm_fund]` raw features),
- augmented matrix = baseline + `cascade_prob`,
then calls `incremental_cv` with `expanding_folds(len(y))` and prints the dict.

Run: `python -m scripts.backtest_cascade_sim` (with the temporary print)
Expected: a dict with finite `brier_delta`, `auc_delta`, `lr_p`, and per-fold `pos_rate` values that vary across folds (confirms time ordering).

- [ ] **Step 3: Commit**

```bash
git add scripts/backtest_cascade_sim.py
git commit -m "feat(cascade-sim): time-series CV incremental Brier/AUC + LR test"
```

---

## Task 9: Robustness sweep over (k, depth)

Re-backfill `cascade_prob` under 2–3 alternative `(impact_k, depth_frac_of_oi)`
settings spanning the defensible range, into separate columns, and re-run the
incremental comparison for each. If the headline improvement holds across the
range, the mechanism is real; if it appears only at one setting, the model is
fitting the parameter.

**Files:**
- Modify: `scripts/backtest_cascade_sim.py`

- [ ] **Step 1: Add the sweep settings + driver**

```python
# append to scripts/backtest_cascade_sim.py

# (impact_k, depth_frac_of_oi) settings: low / default / high impact regimes.
ROBUSTNESS_SETTINGS = [
    ("lo", 0.25, 0.8),   # weaker impact, deeper book -> fewer cascades
    ("hi", 0.60, 0.3),   # stronger impact, thinner book -> more cascades
]


def run_robustness(conn: sqlite3.Connection) -> dict:
    """Backfill cascade_prob under alternative (k, depth) and score each.

    Returns {setting_name: incremental_cv_result}. The default setting is scored
    separately in main(); this covers only the alternatives.
    """
    results = {}
    rows = load_battles(conn)  # already timestamp-sorted
    y = np.array([r["hypothesis_correct"] for r in rows])
    folds = expanding_folds(len(y))
    base = np.array([[baseline_scores(r)[1]] for r in rows])  # baseline_clean

    for name, k, depth_frac in ROBUSTNESS_SETTINGS:
        col = f"cascade_prob_{name}"
        params = CascadeParams(impact_k=k, depth_frac_of_oi=depth_frac)
        ensure_columns(conn, [col, f"cascade_prob_fo_{name}"])
        for r in rows:
            seed = 1234 + int(r["id"])
            feats = dict(
                snapshot_price=r["snapshot_price"],
                total_long_usd=r["total_long_usd"],
                total_short_usd=r["total_short_usd"],
                oi_start_usd=r["oi_start_usd"] or (r["total_long_usd"] + r["total_short_usd"]),
                bigger_side=r["bigger_side"],
            )
            rx = cascade_probability(**feats, params=params, seed=seed)
            conn.execute(f"UPDATE battles_v2 SET {col}=? WHERE id=?", (rx, r["id"]))
        conn.commit()
        cascol = np.array([
            conn.execute(f"SELECT {col} FROM battles_v2 WHERE id=?", (r["id"],)).fetchone()[0]
            for r in rows
        ]).reshape(-1, 1)
        aug = np.hstack([base, cascol])
        results[name] = incremental_cv(base, aug, y, folds)
    return results
```

- [ ] **Step 2: Run and confirm the sweep populates + scores**

Run: `python -m scripts.backtest_cascade_sim` (wire `run_robustness` into main temporarily and print)
Expected: prints two result dicts (`lo`, `hi`) with finite `brier_delta`/`auc_delta`. Confirm columns `cascade_prob_lo`, `cascade_prob_hi` populated for 1075 rows.

- [ ] **Step 3: Commit**

```bash
git add scripts/backtest_cascade_sim.py
git commit -m "feat(cascade-sim): (k,depth) robustness sweep"
```

---

## Task 10: Report generation + final `main()`

Assemble everything into `docs/cascade-sim-backtest-report.md`: chosen `(k, depth)`
+ justification, headline incremental Brier/AUC vs both baselines, the ablation
(`cascade_prob` vs `cascade_prob_fo`), the robustness sweep table, per-fold
tables (n, pos_rate, scores), and the caveats from the spec. Wire a clean
`main()` that runs backfill → score → robustness → write report.

**Files:**
- Modify: `scripts/backtest_cascade_sim.py`

- [ ] **Step 1: Add the report writer + final main()**

```python
# append to scripts/backtest_cascade_sim.py

def _fold_table(folds: list[dict]) -> str:
    head = "| fold | n_test | pos_rate | brier_base | brier_aug | auc_base | auc_aug |\n"
    head += "|---|---|---|---|---|---|---|\n"
    body = ""
    for i, f in enumerate(folds):
        body += (f"| {i} | {f['n_test']} | {f['pos_rate']:.3f} | "
                 f"{f['brier_base']:.4f} | {f['brier_aug']:.4f} | "
                 f"{f['auc_base']:.4f} | {f['auc_aug']:.4f} |\n")
    return head + body


def write_report(path: Path, *, params, default_clean, default_full,
                 ablation, robustness) -> None:
    lines = []
    A = lines.append
    A("# Liquidation Cascade ABM — Backtest Report\n")
    A("**Offline research only — not wired into live trading.**\n")
    A("Predicts `battles_v2.hypothesis_correct` (1075 rows). "
      "Headline metric (pre-committed): **incremental Brier**; secondary: incremental AUC. "
      "Cross-validation: expanding-window time-series folds.\n")

    A("## Chosen parameters & justification\n")
    A(f"- `impact_k = {params.impact_k}`, `depth = {params.depth_frac_of_oi} * oi_start_usd`. "
      "Square-root market-impact form `dp = k*sqrt(notional/depth)` is the standard "
      "microstructure shape. The `_usd` pools are a relative unit, so the impact scale "
      "is relative across battles (fine for ranking). These values are stress-tested "
      "in the robustness sweep below.\n")
    A(f"- Population: {params.n_agents} agents, leverage weights {params.leverage_weights}, "
      f"maintenance haircut {params.maint_haircut}, entry window +/-{params.entry_window_pct}.\n")
    A(f"- Shock drawn per sim ~ N({params.shock_center_pct}, {params.shock_std_pct}); "
      f"{params.n_sims} sims; success threshold {params.success_move_pct} (mirrors RESOLVE_MOVE_PCT).\n")

    def block(title, res):
        A(f"### {title}\n")
        A(f"- Incremental Brier: base {res['brier_base_mean']:.4f} -> "
          f"aug {res['brier_aug_mean']:.4f}  (delta {res['brier_delta']:+.4f}; >0 = better)\n")
        A(f"- Incremental AUC: base {res['auc_base_mean']:.4f} -> "
          f"aug {res['auc_aug_mean']:.4f}  (delta {res['auc_delta']:+.4f}; std {res['auc_aug_std']:.4f})\n")
        A(f"- LR test: stat {res['lr_stat']:.2f}, df {res['extra_df']}, p {res['lr_p']:.4g}\n")
        A(_fold_table(res["folds"]))

    A("## Headline: cascade_prob added to lookahead-clean baseline\n")
    block("vs baseline_clean (imbalance + funding)", default_clean)
    A("## Reference: cascade_prob added to full baseline (includes lookahead OI)\n")
    block("vs baseline_full", default_full)
    A("## Ablation: reflexive cascade_prob vs first-order cascade_prob_fo\n")
    block("first-order added to baseline_clean", ablation)
    A("## Robustness sweep over (k, depth)\n")
    for name, res in robustness.items():
        block(f"setting '{name}'", res)

    A("## Caveats\n")
    A("- Synthetic population != real positions; shows whether the mechanism has "
      "predictive structure, not a tradeable number.\n")
    A("- `_usd` pool unit ambiguity => relative impact scale.\n")
    A("- A negative result (cascade_prob does not beat baseline_clean) is a valid finding.\n")

    path.write_text("\n".join(lines))


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"ERROR: {DB_PATH} not found")
    conn = sqlite3.connect(DB_PATH)
    params = CascadeParams()

    print("Backfilling default cascade_prob + cascade_prob_fo ...")
    backfill(conn, params, "cascade_prob", "cascade_prob_fo")

    rows = load_battles(conn)
    y = np.array([r["hypothesis_correct"] for r in rows])
    folds = expanding_folds(len(y))
    base_clean = np.array([[baseline_scores(r)[1]] for r in rows])
    base_full = np.array([[baseline_scores(r)[0]] for r in rows])
    casc = np.array([
        conn.execute("SELECT cascade_prob FROM battles_v2 WHERE id=?", (r["id"],)).fetchone()[0]
        for r in rows
    ]).reshape(-1, 1)
    casc_fo = np.array([
        conn.execute("SELECT cascade_prob_fo FROM battles_v2 WHERE id=?", (r["id"],)).fetchone()[0]
        for r in rows
    ]).reshape(-1, 1)

    print("Scoring ...")
    default_clean = incremental_cv(base_clean, np.hstack([base_clean, casc]), y, folds)
    default_full = incremental_cv(base_full, np.hstack([base_full, casc]), y, folds)
    ablation = incremental_cv(base_clean, np.hstack([base_clean, casc_fo]), y, folds)

    print("Robustness sweep ...")
    robustness = run_robustness(conn)

    write_report(REPORT_PATH, params=params, default_clean=default_clean,
                 default_full=default_full, ablation=ablation, robustness=robustness)
    print(f"Report written to {REPORT_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full pipeline end-to-end**

Run: `python -m scripts.backtest_cascade_sim`
Expected: prints progress, ends with `Report written to docs/cascade-sim-backtest-report.md`. Open the report and confirm all sections render with finite numbers and per-fold `pos_rate` varies across folds.

- [ ] **Step 3: Full test suite green**

Run: `python -m pytest tests/liqhunt/test_cascade_sim.py -v`
Expected: ALL PASS.

- [ ] **Step 4: Commit (script + report)**

```bash
git add scripts/backtest_cascade_sim.py docs/cascade-sim-backtest-report.md
git commit -m "feat(cascade-sim): report generation + end-to-end backtest pipeline"
```

---

## Done criteria

- [ ] `tests/liqhunt/test_cascade_sim.py` passes (population aggregates, monotonicity, reflexive ≥ first-order invariant, determinism, degenerate sanity, pool-density sensitivity).
- [ ] `battles_v2` has `cascade_prob`, `cascade_prob_fo` (+ robustness columns) populated for all 1075 rows, written non-destructively.
- [ ] `docs/cascade-sim-backtest-report.md` reports incremental Brier (headline) + AUC (secondary) vs `baseline_clean` and `baseline_full`, the first-order ablation, the (k, depth) robustness sweep, per-fold tables, chosen-parameter justification, and caveats.
- [ ] No live trading file (`signal_engine.py`, `quality.py`, `client.py`) was modified.
- [ ] No resolution-time field is ever passed into a `cascade_sim` function (lookahead clean).