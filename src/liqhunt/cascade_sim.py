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
    shock_center_pct: float = 0.003     # mean initial shock toward magnet (0.3%)
    shock_std_pct: float = 0.0015      # stdev of the per-sim shock draw

    # Price impact: dp_fraction = impact_k * sqrt(liquidated_notional / depth)
    impact_k: float = 0.4
    depth_frac_of_oi: float = 0.5       # depth = depth_frac_of_oi * oi_start_usd

    # Monte Carlo + success criterion
    n_sims: int = 200
    success_move_pct: float = 0.005     # mirrors RESOLVE_MOVE_PCT
    max_cascade_steps: int = 50         # safety cap on the feedback loop
