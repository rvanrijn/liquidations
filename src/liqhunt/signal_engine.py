# src/liqhunt/signal_engine.py
"""Core decision engine — evaluates magnet + candles → Signal or None."""

import logging
from datetime import datetime, timezone
from time import time

from src.liqhunt.models import Candle, Signal, SweepResult
from src.liqhunt.sweep import ZONE_PCT, validate_sweep
from src.liqlevels.models import LiqSnapshot
from src.liqhunt.quality import (
    USE_SIGMOID_GATE,
    compute_quality_score,
    sigmoid_gate,
    compute_notional_scale,
)

logger = logging.getLogger(__name__)

MAX_RISK_PCT = 0.01  # 1.0% (was 0.6%)
MIN_IMBALANCE = 1.3
MAX_FAILED_SWEEPS = 2
MAGNET_FLIP_WINDOW = 3  # candles
# Liquidation Storm filter — only trade during active liq cascades + volatile markets
OI_GATE_PCT = -0.3  # OI must drop at least 0.3% (deeper cascade required)
OI_VELOCITY_MIN = -0.05  # OI must drop at least 0.05%/min (cascade velocity)
VOL_RANGE_MIN = 1500  # 6h price range must be >= $1500 (market heat)
SKIP_DAYS = {5}  # Saturday=5 — worst win rate (67%)
TAKER_OPPOSE_THRESHOLD = 1.15  # block when opposing flow is >= 1.15x


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
        liq_levels_long: list[tuple[float, float]],
        liq_levels_short: list[tuple[float, float]],
        btc_delta_1m: float = 0.0,
        oi_change_pct: float = 0.0,
        funding_rate: float = 0.0,
        price_range_6h: float = 0.0,
        oi_velocity: float = 0.0,
        liq_long_usd: float = 0.0,
        liq_short_usd: float = 0.0,
        taker_ratio: float = 1.0,
        cvd_30m: float = 0.0,
    ) -> Signal | None:
        """Run the full decision tree. Returns Signal or None."""

        # 0. Day-of-week filter — skip low-WR days
        today = datetime.now(timezone.utc).weekday()
        if today in SKIP_DAYS:
            day_name = datetime.now(timezone.utc).strftime("%A")
            self.rejection_reason = f"Day filter: {day_name} skipped"
            return None

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
        # Extract price-only lists for sweep validation and targets
        liq_prices_long = [p for p, _ in liq_levels_long]
        liq_prices_short = [p for p, _ in liq_levels_short]

        # Magnet price = nearest liq level on the bigger side (from live data)
        if magnet_side == "LONG" and liq_prices_long:
            magnet_price = max(liq_prices_long)  # closest long liq (highest below price)
        elif magnet_side == "SHORT" and liq_prices_short:
            magnet_price = min(liq_prices_short)  # closest short liq (lowest above price)
        else:
            self.rejection_reason = "No liq levels available"
            return None

        # Track magnet flips
        if self._prev_magnet_side and self._prev_magnet_side != magnet_side:
            self._magnet_flip_ts = time()
        self._prev_magnet_side = magnet_side

        if len(candles) < 3:
            self.rejection_reason = "Insufficient candle data"
            return None

        # 1b. OI regime gate — boolean flags for legacy fallback
        oi_blocked = oi_change_pct > OI_GATE_PCT

        # 1c. Volatility gate
        vol_blocked = price_range_6h < VOL_RANGE_MIN

        # 1d. Cascade velocity gate
        vel_blocked = oi_velocity > OI_VELOCITY_MIN  # too slow (less negative)

        # 1e. Taker flow gate — opposing taker flow may stall cascade
        taker_blocked = False
        if magnet_side == "LONG" and taker_ratio > TAKER_OPPOSE_THRESHOLD:
            taker_blocked = True
        elif magnet_side == "SHORT" and taker_ratio < (1 / TAKER_OPPOSE_THRESHOLD):
            taker_blocked = True

        # 2. Sweep validation
        liq_prices = liq_prices_long if magnet_side == "LONG" else liq_prices_short
        sweep = validate_sweep(candles, magnet_price, magnet_side, avg_range, liq_prices)

        if sweep is None:
            # Near-miss: how close did the candle get to the zone?
            miss = ""
            if len(candles) >= 2 and magnet_price > 0:
                sc = candles[-2]
                if magnet_side == "LONG":
                    zone_edge = magnet_price * (1 + ZONE_PCT)
                    dist_pct = (sc.low - zone_edge) / magnet_price * 100
                    miss = f" | low {dist_pct:+.2f}% from zone"
                else:
                    zone_edge = magnet_price * (1 - ZONE_PCT)
                    dist_pct = (sc.high - zone_edge) / magnet_price * 100
                    miss = f" | high {dist_pct:+.2f}% from zone"
            self.rejection_reason = f"No sweep toward {magnet_side} magnet{miss}"
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

        # 4b. Orderflow confirmation — delta must align with cascade direction
        # LONG magnet = cascade DOWN = delta should be negative (selling)
        if magnet_side == "LONG" and btc_delta_1m > 0:
            self.rejection_reason = (
                f"Orderflow opposes cascade (1m delta ${btc_delta_1m / 1e6:+.1f}M, need selling)"
            )
            return None
        elif magnet_side == "SHORT" and btc_delta_1m < 0:
            self.rejection_reason = (
                f"Orderflow opposes cascade (1m delta ${btc_delta_1m / 1e6:+.1f}M, need buying)"
            )
            return None

        # 5. Risk check
        # Storm/momentum: trade WITH the cascade, not against it
        # LONG magnet = longs getting liquidated = price dropping = go SHORT
        # Stop on the loss side: mirror sweep.extreme across entry
        if magnet_side == "LONG":
            direction = "SHORT"
            entry_price = hold_candle.close
            stop_price = entry_price + abs(entry_price - sweep.extreme)  # above entry
        else:
            direction = "LONG"
            entry_price = hold_candle.close
            stop_price = entry_price - abs(sweep.extreme - entry_price)  # below entry

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
            direction, entry_price, liq_prices_long, liq_prices_short, snapshot,
        )

        # 7. Confidence score + density
        _, density_ratio = self._compute_density(
            btc_price,
            liq_levels_long if magnet_side == "LONG" else liq_levels_short,
            magnet_side,
        )
        confidence = self._compute_confidence(
            imbalance_ratio=snapshot.imbalance_ratio,
            criteria_met=sweep.criteria_met,
            btc_delta_1m=btc_delta_1m,
            funding_rate=funding_rate,
            magnet_side=magnet_side,
            density_ratio=density_ratio,
            liq_long_usd=liq_long_usd,
            liq_short_usd=liq_short_usd,
            taker_ratio=taker_ratio,
        )

        # Build reasoning
        swept_side = "longs" if magnet_side == "LONG" else "shorts"
        oi_label = "liqs" if oi_change_pct < 0 else "new pos"
        fr_pct = funding_rate * 100
        liq_total = liq_long_usd + liq_short_usd
        reasoning = (
            f"{swept_side.title()} cascade active — {direction} with momentum. "
            f"OI {oi_change_pct:+.1f}% ({oi_label}), vel {oi_velocity:+.3f}%/min. "
            f"FR {fr_pct:+.4f}%. "
            f"Density {density_ratio:.0%}. "
            f"Liq feed: ${liq_long_usd / 1e6:.1f}M longs / ${liq_short_usd / 1e6:.1f}M shorts (5min). "
            f"Taker {taker_ratio:.2f}x. "
            f"CVD ${cvd_30m / 1e6:+.1f}M. "
            f"Confidence {confidence}/100. "
            f"Target ${primary_target:,.0f}."
        )

        # ── Quality gate (sigmoid) ───────────────────────────────
        if USE_SIGMOID_GATE:
            q = compute_quality_score(
                oi_change_pct=oi_change_pct,
                imbalance_ratio=snapshot.imbalance_ratio,
                funding_rate=funding_rate,
                price_range_6h=price_range_6h,
                oi_velocity=oi_velocity,
                taker_ratio=taker_ratio,
                magnet_side=magnet_side,
            )
            p = sigmoid_gate(q)
            ns = compute_notional_scale(p)

            if ns == 0.0:
                logger.warning(
                    "QUALITY GATE REJECT: %s %s @ %.0f | q=%.3f p=%.3f < p_min | OI %+.2f%% imb %.2fx",
                    direction, magnet_side, entry_price, q, p,
                    oi_change_pct, snapshot.imbalance_ratio,
                )
                self.rejection_reason = (
                    f"Quality too low (q={q:.2f} p={p:.2f}) "
                    f"[shadow: {direction} @ {entry_price:,.0f}]"
                )
                return None

            logger.warning(
                "QUALITY GATE PASS: %s %s @ %.0f | q=%.3f p=%.3f size=%.1fx | OI %+.2f%% imb %.2fx",
                direction, magnet_side, entry_price, q, p, ns,
                oi_change_pct, snapshot.imbalance_ratio,
            )
        else:
            # Legacy binary gates — shadow-log and suppress
            if oi_blocked:
                if oi_change_pct > 0:
                    reason = f"OI regime: new positions ({oi_change_pct:+.2f}%)"
                else:
                    reason = f"OI regime: flat ({oi_change_pct:+.2f}% > {OI_GATE_PCT}%)"
                logger.warning(
                    "SHADOW TRADE blocked by OI gate: %s %s @ %.0f | %s",
                    direction, magnet_side, entry_price, reason,
                )
                self.rejection_reason = reason + f" [shadow: {direction} @ {entry_price:,.0f}]"
                return None

            if vol_blocked:
                reason = f"Low volatility: 6h range ${price_range_6h:,.0f} < ${VOL_RANGE_MIN:,}"
                logger.warning(
                    "SHADOW TRADE blocked by VOL gate: %s %s @ %.0f | %s",
                    direction, magnet_side, entry_price, reason,
                )
                self.rejection_reason = reason + f" [shadow: {direction} @ {entry_price:,.0f}]"
                return None

            if vel_blocked:
                reason = f"Cascade too slow: OI vel {oi_velocity:+.3f}%/min > {OI_VELOCITY_MIN}%/min"
                logger.warning(
                    "SHADOW TRADE blocked by VELOCITY gate: %s %s @ %.0f | %s",
                    direction, magnet_side, entry_price, reason,
                )
                self.rejection_reason = reason + f" [shadow: {direction} @ {entry_price:,.0f}]"
                return None

            if taker_blocked:
                if magnet_side == "LONG":
                    reason = f"Taker flow opposes: buying {taker_ratio:.2f}x vs SHORT cascade"
                else:
                    reason = f"Taker flow opposes: selling {taker_ratio:.2f}x vs LONG cascade"
                logger.warning(
                    "SHADOW TRADE blocked by TAKER gate: %s %s @ %.0f | %s",
                    direction, magnet_side, entry_price, reason,
                )
                self.rejection_reason = reason + f" [shadow: {direction} @ {entry_price:,.0f}]"
                return None

            q, p, ns = 0.0, 0.0, 1.0

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
            confidence=confidence,
            quality_score=q,
            gate_probability=p,
            notional_scale=ns,
        )

    def _compute_confidence(
        self,
        imbalance_ratio: float,
        criteria_met: list[str],
        btc_delta_1m: float,
        funding_rate: float,
        magnet_side: str,
        density_ratio: float,
        liq_long_usd: float,
        liq_short_usd: float,
        taker_ratio: float = 1.0,
    ) -> int:
        """Compute signal confidence score (0-100)."""
        score = 0

        # Imbalance strength: 1.5x=5, 2.0x=15, 3.0x+=25
        if imbalance_ratio >= 3.0:
            score += 25
        elif imbalance_ratio >= 2.0:
            score += 15
        else:
            score += 5

        # Sweep quality: 3 criteria=15, 4+=25
        n = len(criteria_met)
        if n >= 4:
            score += 25
        elif n >= 3:
            score += 15

        # Orderflow strength: |delta| > $10M = 15, > $30M = 25
        delta_abs = abs(btc_delta_1m)
        if delta_abs > 30e6:
            score += 25
        elif delta_abs > 10e6:
            score += 15

        # Funding alignment
        aligned = (magnet_side == "LONG" and funding_rate > 0) or (
            magnet_side == "SHORT" and funding_rate < 0
        )
        if aligned:
            if abs(funding_rate) > 0.0003:
                score += 25  # strong
            else:
                score += 15

        # Cluster density
        if density_ratio > 0.7:
            score += 25
        elif density_ratio > 0.5:
            score += 15

        # Taker flow alignment
        if taker_ratio != 1.0:
            taker_aligned = (
                (magnet_side == "LONG" and taker_ratio < 1.0) or
                (magnet_side == "SHORT" and taker_ratio > 1.0)
            )
            if taker_aligned:
                score += 10  # flow confirms cascade direction
            else:
                score -= 10  # flow opposes cascade

        # Real-time liq feed confirmation
        if liq_long_usd + liq_short_usd > 0:
            if magnet_side == "LONG" and liq_long_usd > liq_short_usd:
                score += 15  # longs being liquidated confirms LONG magnet
            elif magnet_side == "SHORT" and liq_short_usd > liq_long_usd:
                score += 15  # shorts being liquidated confirms SHORT magnet
            else:
                score -= 15  # wrong side getting liquidated

        return max(0, score)

    def _compute_density(
        self,
        btc_price: float,
        liq_levels_with_usd: list[tuple[float, float]],
        magnet_side: str,
    ) -> tuple[float, float]:
        """Compute fraction of liq USD within 2% of price.

        Returns (near_usd, density_ratio).
        """
        if not liq_levels_with_usd:
            return 0.0, 0.0
        total = sum(usd for _, usd in liq_levels_with_usd)
        if total <= 0:
            return 0.0, 0.0
        near_pct = 0.02  # 2%
        near = sum(
            usd
            for price, usd in liq_levels_with_usd
            if abs(price - btc_price) / btc_price <= near_pct
        )
        return near, near / total

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
            primary = above[0] if above else entry * 1.01
            secondary = above[1] if len(above) > 1 else entry * 1.02
        else:
            # Target: nearest long liq levels (below)
            below = sorted([p for p in liq_long if p < entry], reverse=True)
            primary = below[0] if below else entry * 0.99
            secondary = below[1] if len(below) > 1 else entry * 0.98

        return primary, secondary
