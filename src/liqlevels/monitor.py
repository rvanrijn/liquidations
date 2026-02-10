# src/liqlevels/monitor.py
"""Magnet monitor — tracks whether price sweeps the bigger liquidation side first."""

import logging
from time import time

from src.liqlevels.client import CoinLiquidations
from src.liqlevels.database import MagnetDatabase
from src.liqlevels.models import Battle, LiqSnapshot

logger = logging.getLogger(__name__)

# Price within 0.05% of nearest liq level counts as a "hit"
HIT_THRESHOLD_PCT = 0.05
# Minimum imbalance to start tracking (skip near-equal sides)
MIN_IMBALANCE_RATIO = 1.1


class MagnetMonitor:
    """Core engine: snapshot liq levels, track price, resolve battles.

    Target prices are locked at snapshot time and don't move with
    recalculated levels. A battle invalidates only when the imbalance
    flips sides (bigger side changes from LONG to SHORT or vice versa).
    """

    def __init__(self, db: MagnetDatabase):
        self.db = db
        self.snapshot: LiqSnapshot | None = None
        self.stats = db.get_stats()
        self.recent_battles = db.get_recent_battles()

    def update(self, btc_data: CoinLiquidations) -> Battle | None:
        """Called every poll with fresh BTC data. Returns a Battle if one was just resolved."""
        price = btc_data.current_price
        nearest_long = btc_data.longs_at_risk[0] if btc_data.longs_at_risk else None
        nearest_short = btc_data.shorts_at_risk[0] if btc_data.shorts_at_risk else None

        if not nearest_long or not nearest_short:
            return None

        total_long_usd = sum(l.estimated_usd for l in btc_data.longs_at_risk)
        total_short_usd = sum(l.estimated_usd for l in btc_data.shorts_at_risk)

        # No active snapshot → try to create one
        if self.snapshot is None:
            self._try_snapshot(price, nearest_long.price, nearest_short.price,
                               total_long_usd, total_short_usd)
            return None

        # Invalidate if imbalance flipped sides
        current_bigger = "LONG" if total_long_usd >= total_short_usd else "SHORT"
        if current_bigger != self.snapshot.bigger_side:
            logger.debug("Imbalance flipped from %s to %s, resetting", self.snapshot.bigger_side, current_bigger)
            self.snapshot = None
            self._try_snapshot(price, nearest_long.price, nearest_short.price,
                               total_long_usd, total_short_usd)
            return None

        # Check if price hit either side (using locked snapshot targets)
        battle = self._check_hit(price)
        if battle:
            self.db.log_battle(battle)
            self.stats = self.db.get_stats()
            self.recent_battles = self.db.get_recent_battles()
            self.snapshot = None
            # Try to start a new snapshot immediately
            self._try_snapshot(price, nearest_long.price, nearest_short.price,
                               total_long_usd, total_short_usd)
        return battle

    def _try_snapshot(self, price: float, long_price: float, short_price: float,
                      long_usd: float, short_usd: float) -> None:
        """Create a new snapshot if imbalance is large enough."""
        bigger = max(long_usd, short_usd)
        smaller = min(long_usd, short_usd)
        if smaller > 0 and bigger / smaller >= MIN_IMBALANCE_RATIO:
            self.snapshot = LiqSnapshot(
                btc_price=price,
                nearest_long_price=long_price,
                nearest_short_price=short_price,
                total_long_usd=long_usd,
                total_short_usd=short_usd,
                timestamp=time(),
            )

    def _check_hit(self, price: float) -> Battle | None:
        """Check if price reached within HIT_THRESHOLD_PCT of either side's nearest level."""
        snap = self.snapshot

        # Distance to long liq (price needs to DROP to hit longs)
        long_dist_pct = (price - snap.nearest_long_price) / snap.nearest_long_price * 100
        # Distance to short liq (price needs to RISE to hit shorts)
        short_dist_pct = (snap.nearest_short_price - price) / snap.nearest_short_price * 100

        hit_side = None
        if long_dist_pct <= HIT_THRESHOLD_PCT:
            hit_side = "LONG"
        elif short_dist_pct <= HIT_THRESHOLD_PCT:
            hit_side = "SHORT"

        if hit_side is None:
            return None

        now = time()
        return Battle(
            bigger_side=snap.bigger_side,
            hit_side=hit_side,
            hypothesis_correct=(hit_side == snap.bigger_side),
            imbalance_ratio=snap.imbalance_ratio,
            duration_seconds=now - snap.timestamp,
            snapshot_price=snap.btc_price,
            nearest_long_price=snap.nearest_long_price,
            nearest_short_price=snap.nearest_short_price,
            total_long_usd=snap.total_long_usd,
            total_short_usd=snap.total_short_usd,
            resolved_price=price,
            timestamp=now,
        )
