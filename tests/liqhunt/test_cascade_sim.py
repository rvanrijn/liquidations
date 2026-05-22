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