#!/usr/bin/env python3
"""Calibrate sigmoid quality gate parameters against battle data.

Sweeps k, q0, p_min, w_oi, w_imb, w_fund across 3,375 combos.
Evaluates each against cascade battles from battles_v2.
Walk-forward: train on first 250, validate on remainder.

Usage: uv run python scripts/calibrate_sigmoid.py
"""

import math
import sqlite3
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path


DB_PATH = Path("data/magnet_battles.db")

# Sweep ranges
K_VALUES = [2.0, 4.0, 6.0, 8.0, 10.0]
Q0_VALUES = [0.2, 0.3, 0.4, 0.5, 0.6]
PMIN_VALUES = [0.3, 0.4, 0.5, 0.6, 0.7]
W_OI_VALUES = [0.2, 0.35, 0.5]
W_IMB_VALUES = [0.2, 0.35, 0.5]
W_FUND_VALUES = [0.0, 0.1, 0.2]

# Fixed weights for features not in battle data
W_VOL_FIXED = 0.10
W_VEL_FIXED = 0.10
W_TAKER_FIXED = 0.10
NEUTRAL_IMPUTE = 0.5  # impute missing features as neutral

# Fees (Kraken)
FEE_MAKER = 0.0002  # 0.02%
FEE_TAKER = 0.0005  # 0.05%

# Position sizing
BASE_NOTIONAL = 25_000.0
NOTIONAL_FLOOR = 0.5
NOTIONAL_CEIL = 2.0

TRAIN_SIZE = 250
MIN_TRADES = 50


@dataclass
class Battle:
    oi_change_pct: float
    imbalance_ratio: float
    funding_rate: float
    bigger_side: str
    snapshot_price: float
    resolved_price: float
    move_pct: float
    timestamp: float


def load_battles() -> list[Battle]:
    """Load cascade battles (OI <= -0.10%) from battles_v2, sorted chronologically."""
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT oi_change_pct, imbalance_ratio, funding_rate, bigger_side,
               snapshot_price, resolved_price, move_pct, timestamp
        FROM battles_v2
        WHERE oi_change_pct <= -0.10
        ORDER BY timestamp ASC
    """).fetchall()
    conn.close()

    return [Battle(**dict(r)) for r in rows]


def compute_quality(
    battle: Battle,
    w_oi: float, w_imb: float, w_fund: float,
) -> float:
    """Compute quality score for a battle using available features."""
    weight_sum = w_oi + w_imb + w_fund + W_VOL_FIXED + W_VEL_FIXED + W_TAKER_FIXED

    # Normalize available features
    norm_oi = min(abs(battle.oi_change_pct) / 1.0, 1.0)
    norm_imb = max(min((battle.imbalance_ratio - 1.0) / 2.0, 1.0), 0.0)

    # Funding alignment
    if battle.bigger_side == "LONG":
        norm_fund = min(max(battle.funding_rate / 0.001, 0.0), 1.0)
    else:
        norm_fund = min(max(-battle.funding_rate / 0.001, 0.0), 1.0)

    q = (
        w_oi * norm_oi
        + w_imb * norm_imb
        + w_fund * norm_fund
        + W_VOL_FIXED * NEUTRAL_IMPUTE
        + W_VEL_FIXED * NEUTRAL_IMPUTE
        + W_TAKER_FIXED * NEUTRAL_IMPUTE
    )

    return q / weight_sum


def simulate(
    battles: list[Battle],
    k: float, q0: float, p_min: float,
    w_oi: float, w_imb: float, w_fund: float,
) -> tuple[float, int, float, float]:
    """Simulate trades on a set of battles. Returns (net_pnl, n_trades, pf, wr)."""
    gross_win = 0.0
    gross_loss = 0.0
    n_trades = 0
    n_wins = 0

    for b in battles:
        q = compute_quality(b, w_oi, w_imb, w_fund)
        exponent = -k * (q - q0)
        exponent = max(min(exponent, 500), -500)
        p = 1.0 / (1.0 + math.exp(exponent))

        if p < p_min:
            continue

        # Position sizing
        scale = min((p - p_min) / (1.0 - p_min), 1.0)
        notional = BASE_NOTIONAL * (NOTIONAL_FLOOR + scale * (NOTIONAL_CEIL - NOTIONAL_FLOOR))

        # P&L: direction is SHORT if bigger_side == LONG, else LONG
        if b.bigger_side == "LONG":
            pnl_pct = (b.snapshot_price - b.resolved_price) / b.snapshot_price * 100
        else:
            pnl_pct = (b.resolved_price - b.snapshot_price) / b.snapshot_price * 100

        pnl_usd = pnl_pct / 100 * notional
        fees = notional * FEE_MAKER + notional * FEE_TAKER
        net = pnl_usd - fees

        n_trades += 1
        if net > 0:
            gross_win += net
            n_wins += 1
        else:
            gross_loss += abs(net)

    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
    wr = n_wins / n_trades * 100 if n_trades > 0 else 0.0
    net_pnl = gross_win - gross_loss
    return net_pnl, n_trades, pf, wr


@dataclass
class Result:
    k: float
    q0: float
    p_min: float
    w_oi: float
    w_imb: float
    w_fund: float
    train_pnl: float
    train_trades: int
    train_pf: float
    train_wr: float
    val_pnl: float
    val_trades: int
    val_pf: float
    val_wr: float


def main():
    battles = load_battles()
    print(f"Loaded {len(battles)} cascade battles (OI <= -0.10%)")

    if len(battles) < TRAIN_SIZE + 10:
        print(f"ERROR: Need at least {TRAIN_SIZE + 10} battles, got {len(battles)}")
        sys.exit(1)

    train = battles[:TRAIN_SIZE]
    val = battles[TRAIN_SIZE:]
    print(f"Train: {len(train)} | Validation: {len(val)}")

    combos = list(product(K_VALUES, Q0_VALUES, PMIN_VALUES, W_OI_VALUES, W_IMB_VALUES, W_FUND_VALUES))
    print(f"Sweeping {len(combos)} parameter combinations...")

    results: list[Result] = []
    for i, (k, q0, p_min, w_oi, w_imb, w_fund) in enumerate(combos):
        train_pnl, train_n, train_pf, train_wr = simulate(train, k, q0, p_min, w_oi, w_imb, w_fund)
        if train_n < MIN_TRADES:
            continue

        val_pnl, val_n, val_pf, val_wr = simulate(val, k, q0, p_min, w_oi, w_imb, w_fund)

        results.append(Result(
            k=k, q0=q0, p_min=p_min, w_oi=w_oi, w_imb=w_imb, w_fund=w_fund,
            train_pnl=train_pnl, train_trades=train_n, train_pf=train_pf, train_wr=train_wr,
            val_pnl=val_pnl, val_trades=val_n, val_pf=val_pf, val_wr=val_wr,
        ))

        if (i + 1) % 500 == 0:
            print(f"  {i + 1}/{len(combos)} combos evaluated, {len(results)} qualify...")

    # Sort by train P&L (primary), profit factor (secondary)
    results.sort(key=lambda r: (r.train_pnl, r.train_pf), reverse=True)

    print(f"\n{'='*100}")
    print(f"TOP 10 BY TRAIN P&L (min {MIN_TRADES} trades)")
    print(f"{'='*100}")
    print(f"{'k':>4} {'q0':>4} {'pmin':>4} {'w_oi':>5} {'w_imb':>5} {'w_f':>4} | "
          f"{'T_PnL':>8} {'T_N':>4} {'T_PF':>5} {'T_WR':>5} | "
          f"{'V_PnL':>8} {'V_N':>4} {'V_PF':>5} {'V_WR':>5}")
    print("-" * 100)

    for r in results[:10]:
        flag = " *" if r.val_pf >= 1.0 else "  "
        print(
            f"{r.k:4.1f} {r.q0:4.2f} {r.p_min:4.2f} {r.w_oi:5.2f} {r.w_imb:5.2f} {r.w_fund:4.2f} | "
            f"${r.train_pnl:>7,.0f} {r.train_trades:>4} {r.train_pf:>5.2f} {r.train_wr:>4.1f}% | "
            f"${r.val_pnl:>7,.0f} {r.val_trades:>4} {r.val_pf:>5.2f} {r.val_wr:>4.1f}%{flag}"
        )

    # Best validated config
    validated = [r for r in results if r.val_pf >= 1.0 and r.val_trades >= 20]
    if validated:
        best = max(validated, key=lambda r: r.train_pnl)
        print(f"\n{'='*100}")
        print("BEST VALIDATED CONFIG (val PF >= 1.0, val trades >= 20):")
        print(f"{'='*100}")
        print(f"""
# Paste into src/liqhunt/quality.py:
W_OI = {best.w_oi}
W_IMB = {best.w_imb}
W_FUND = {best.w_fund}
SIGMOID_K = {best.k}
SIGMOID_Q0 = {best.q0}
P_MIN = {best.p_min}

# Train: ${best.train_pnl:,.0f} PnL | {best.train_trades} trades | PF {best.train_pf:.2f} | WR {best.train_wr:.1f}%
# Val:   ${best.val_pnl:,.0f} PnL | {best.val_trades} trades | PF {best.val_pf:.2f} | WR {best.val_wr:.1f}%
""")
    else:
        print("\nWARNING: No configs passed validation (PF >= 1.0 on holdout set)")
        if results:
            best_train = results[0]
            print(f"Best train-only: k={best_train.k} q0={best_train.q0} pmin={best_train.p_min}")


if __name__ == "__main__":
    main()
