"""Sigmoid quality gate for the liqhunt signal engine.

Replaces 4 binary pass/fail gates with a continuous quality score
fed through a logistic sigmoid. Calibrated against 374 cascade
battles from battles_v2.

Feature flag USE_SIGMOID_GATE allows instant rollback to binary gates.
"""

import math

# ── Feature flag ──────────────────────────────────────────────
USE_SIGMOID_GATE = True

# ── Calibrated weights (updated by scripts/calibrate_sigmoid.py) ──
# Data-backed features (calibrated via sweep)
W_OI = 0.35
W_IMB = 0.30
W_FUND = 0.10

# Live-only features (fixed at 0.1, not in battle data)
W_VOL = 0.10
W_VEL = 0.10
W_TAKER = 0.10

WEIGHT_SUM = W_OI + W_IMB + W_FUND + W_VOL + W_VEL + W_TAKER

# ── Sigmoid parameters (updated by scripts/calibrate_sigmoid.py) ──
SIGMOID_K = 6.0
SIGMOID_Q0 = 0.40
P_MIN = 0.50

# ── Position sizing ──────────────────────────────────────────
BASE_NOTIONAL = 25_000.0  # current fixed $25K becomes midpoint
NOTIONAL_FLOOR = 0.5      # minimum 0.5x ($12.5K)
NOTIONAL_CEIL = 2.0       # maximum 2.0x ($50K)


def compute_quality_score(
    oi_change_pct: float,
    imbalance_ratio: float,
    funding_rate: float,
    price_range_6h: float,
    oi_velocity: float,
    taker_ratio: float,
    magnet_side: str,
) -> float:
    """Compute normalized quality score q in [0, 1] from 6 features.

    Each feature is normalized to [0, 1] where 1 = best signal quality,
    then combined as weighted average.
    """
    # 1. OI depth: deeper drop = better
    norm_oi = min(abs(oi_change_pct) / 1.0, 1.0)

    # 2. Imbalance: higher ratio = better
    norm_imb = min((imbalance_ratio - 1.0) / 2.0, 1.0)
    norm_imb = max(norm_imb, 0.0)

    # 3. Funding alignment: directional
    # LONG magnet + positive funding = aligned (longs paying = crowded)
    # SHORT magnet + negative funding = aligned (shorts paying = crowded)
    if magnet_side == "LONG":
        norm_fund = min(max(funding_rate / 0.001, 0.0), 1.0)  # positive = aligned
    else:
        norm_fund = min(max(-funding_rate / 0.001, 0.0), 1.0)  # negative = aligned

    # 4. Volatility: higher 6h range = better
    norm_vol = min(price_range_6h / 3000.0, 1.0)

    # 5. OI velocity: faster drop = better (more negative = better)
    norm_vel = min(abs(oi_velocity) / 0.2, 1.0)

    # 6. Taker alignment: directional
    # SHORT trade (LONG magnet): ideal ratio ~0.7 (sellers dominate)
    # LONG trade (SHORT magnet): ideal ratio ~1.3 (buyers dominate)
    if magnet_side == "LONG":
        ideal = 0.7
    else:
        ideal = 1.3
    norm_taker = 1.0 - min(abs(taker_ratio - ideal) / 0.5, 1.0)

    q = (
        W_OI * norm_oi
        + W_IMB * norm_imb
        + W_FUND * norm_fund
        + W_VOL * norm_vol
        + W_VEL * norm_vel
        + W_TAKER * norm_taker
    )

    return q / WEIGHT_SUM


def sigmoid_gate(quality_score: float) -> float:
    """Apply logistic sigmoid to quality score.

    Returns probability p in [0, 1].
    p(q) = 1 / (1 + e^(-k * (q - q0)))
    """
    exponent = -SIGMOID_K * (quality_score - SIGMOID_Q0)
    # Clamp to avoid overflow
    exponent = max(min(exponent, 500), -500)
    return 1.0 / (1.0 + math.exp(exponent))


def compute_notional_scale(probability: float) -> float:
    """Compute position size multiplier from gate probability.

    Linear ramp from p_min to 1.0 -> scale 0.5x to 2.0x.
    """
    if probability < P_MIN:
        return 0.0  # no trade
    scale = min((probability - P_MIN) / (1.0 - P_MIN), 1.0)
    return NOTIONAL_FLOOR + scale * (NOTIONAL_CEIL - NOTIONAL_FLOOR)