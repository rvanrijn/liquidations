# src/liqhunt/signal_engine.py
"""Core decision engine — evaluates magnet + candles → Signal or None."""

import logging
from time import time

from src.liqhunt.models import Candle, Signal, SweepResult
from src.liqhunt.sweep import ZONE_PCT, validate_sweep
from src.liqlevels.models import LiqSnapshot

logger = logging.getLogger(__name__)

MAX_RISK_PCT = 0.006  # 0.6%
MIN_IMBALANCE = 1.5
MAX_FAILED_SWEEPS = 2
MAGNET_FLIP_WINDOW = 3  # candles


class SignalEngine:
    """Evaluates market state and produces trade signals."""

    def __init__(self):
        self.last_signal_ts: float = 0
        self.last_sweep_dir: str | None = None
        self.failed_sweep_count: int = 0
        self._prev_magnet_side: str | None = None
        self._magnet_flip_ts: float = 0
        # Rejection reason for dashboard display
        self.rejection_reason: str = "Initializing"

    def evaluate(
        self,
        snapshot: LiqSnapshot | None,
        candles: list[Candle],
        avg_range: float,
        btc_price: float,
        liq_levels_long: list[float],
        liq_levels_short: list[float],
    ) -> Signal | None:
        """Run the full decision tree. Returns Signal or None."""

        # 1. Bias gate
        if snapshot is None:
            self.rejection_reason = "No magnet snapshot"
            return None

        if snapshot.imbalance_ratio < MIN_IMBALANCE:
            self.rejection_reason = (
                f"Imbalance insufficient ({snapshot.imbalance_ratio:.2f}x < {MIN_IMBALANCE}x)"
            )
            return None

        magnet_side = snapshot.bigger_side
        magnet_price = (
            snapshot.nearest_long_price
            if magnet_side == "LONG"
            else snapshot.nearest_short_price
        )

        # Track magnet flips
        if self._prev_magnet_side and self._prev_magnet_side != magnet_side:
            self._magnet_flip_ts = time()
        self._prev_magnet_side = magnet_side

        if len(candles) < 3:
            self.rejection_reason = "Insufficient candle data"
            return None

        # 7. Hard filter — multiple failed sweeps in same direction
        if self.failed_sweep_count >= MAX_FAILED_SWEEPS:
            self.rejection_reason = (
                f"Multiple failed sweeps ({self.failed_sweep_count}) in same direction"
            )
            return None

        # 2. Sweep validation
        liq_levels = liq_levels_long if magnet_side == "LONG" else liq_levels_short
        sweep = validate_sweep(candles, magnet_price, magnet_side, avg_range, liq_levels)

        if sweep is None:
            self.rejection_reason = f"No sweep toward {magnet_side} magnet"
            return None

        if not sweep.valid:
            self.rejection_reason = (
                f"Sweep invalid ({len(sweep.criteria_met)}/3 criteria: "
                f"{','.join(sweep.criteria_met) or 'none'})"
            )
            # Track failed sweeps
            if self.last_sweep_dir == sweep.direction:
                self.failed_sweep_count += 1
            else:
                self.failed_sweep_count = 1
                self.last_sweep_dir = sweep.direction
            return None

        # Valid sweep found — reset failed counter
        self.failed_sweep_count = 0
        self.last_sweep_dir = sweep.direction

        # 3. Execution zone — confirm it was a spike, not slow drift
        # Already handled by validate_sweep checking zone entry

        # 4. Reclaim-and-hold
        hold_candle = candles[-1]  # the candle after the sweep

        if magnet_side == "LONG":
            # Down sweep → expect reclaim above magnet (price recovers)
            reclaimed = hold_candle.close > magnet_price
            zone_boundary = magnet_price * (1 - ZONE_PCT)
            held = hold_candle.close > zone_boundary
        else:
            # Up sweep → expect reclaim below magnet (price drops back)
            reclaimed = hold_candle.close < magnet_price
            zone_boundary = magnet_price * (1 + ZONE_PCT)
            held = hold_candle.close < zone_boundary

        if not reclaimed or not held:
            self.rejection_reason = (
                f"Reclaim not confirmed (close ${hold_candle.close:,.0f} "
                f"vs magnet ${magnet_price:,.0f})"
            )
            return None

        # 5. Risk check
        if magnet_side == "LONG":
            direction = "LONG"
            entry_price = hold_candle.close
            stop_price = sweep.extreme  # below the sweep low
        else:
            direction = "SHORT"
            entry_price = hold_candle.close
            stop_price = sweep.extreme  # above the sweep high

        risk_pct = abs(entry_price - stop_price) / entry_price
        if risk_pct > MAX_RISK_PCT:
            self.rejection_reason = (
                f"Risk too high ({risk_pct * 100:.2f}% > {MAX_RISK_PCT * 100:.1f}%)"
            )
            return None

        # Dedup — don't signal twice on the same sweep
        if sweep.candle.timestamp <= self.last_signal_ts:
            self.rejection_reason = "Already signaled on this sweep"
            return None

        # 6. Targets
        primary_target, secondary_target = self._compute_targets(
            direction, entry_price, liq_levels_long, liq_levels_short, snapshot,
        )

        # Build reasoning
        swept_side = "longs" if magnet_side == "LONG" else "shorts"
        target_side = "short" if magnet_side == "LONG" else "long"
        reasoning = (
            f"{swept_side.title()} swept at ${sweep.extreme:,.0f}, "
            f"reclaim confirmed above magnet. "
            f"Targeting {target_side} cluster at ${primary_target:,.0f}."
        )

        self.last_signal_ts = sweep.candle.timestamp
        self.rejection_reason = ""

        return Signal(
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            risk_pct=risk_pct,
            primary_target=primary_target,
            secondary_target=secondary_target,
            reasoning=reasoning,
            magnet_side=magnet_side,
            imbalance_ratio=snapshot.imbalance_ratio,
            sweep=sweep,
        )

    def _compute_targets(
        self,
        direction: str,
        entry: float,
        liq_long: list[float],
        liq_short: list[float],
        snapshot: LiqSnapshot,
    ) -> tuple[float, float]:
        """Compute primary and secondary targets based on liquidity."""
        if direction == "LONG":
            # Target: nearest short liq levels (above)
            above = sorted([p for p in liq_short if p > entry])
            primary = above[0] if above else snapshot.nearest_short_price
            secondary = above[1] if len(above) > 1 else snapshot.nearest_short_price
        else:
            # Target: nearest long liq levels (below)
            below = sorted([p for p in liq_long if p < entry], reverse=True)
            primary = below[0] if below else snapshot.nearest_long_price
            secondary = below[1] if len(below) > 1 else snapshot.nearest_long_price

        return primary, secondary
