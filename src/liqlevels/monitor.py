# src/liqlevels/monitor.py
"""Magnet monitor — tracks whether price moves toward the bigger liquidation side first."""

import logging
from time import time

from src.liqlevels.client import CoinLiquidations
from src.liqlevels.database import MagnetDatabase
from src.liqlevels.models import Battle, LiqSnapshot, RESOLVE_MOVE_PCT

logger = logging.getLogger(__name__)

# Minimum imbalance to start tracking (skip near-equal sides)
MIN_IMBALANCE_RATIO = 1.1


class MagnetMonitor:
    """Core engine: snapshot imbalance, track price direction, resolve battles.

    Hypothesis: price moves toward the side with more USD at risk.
    - LONG bigger → expect price to DROP (toward long liquidations)
    - SHORT bigger → expect price to RISE (toward short liquidations)

    Battle resolves when price moves ±RESOLVE_MOVE_PCT from snapshot price.
    """

    def __init__(self, db: MagnetDatabase):
        self.db = db
        self.snapshot: LiqSnapshot | None = None
        self.stats = db.get_stats()
        self.recent_battles = db.get_recent_battles()

    def update(self, btc_data: CoinLiquidations) -> Battle | None:
        """Called every poll with fresh BTC data. Returns a Battle if one was just resolved."""
        price = btc_data.current_price

        total_long_usd = sum(l.estimated_usd for l in btc_data.longs_at_risk)
        total_short_usd = sum(l.estimated_usd for l in btc_data.shorts_at_risk)

        # No active snapshot → try to create one
        if self.snapshot is None:
            self._try_snapshot(price, total_long_usd, total_short_usd)
            return None

        # Invalidate if imbalance flipped sides
        current_bigger = "LONG" if total_long_usd >= total_short_usd else "SHORT"
        if current_bigger != self.snapshot.bigger_side:
            logger.debug("Imbalance flipped from %s to %s, resetting",
                         self.snapshot.bigger_side, current_bigger)
            self.snapshot = None
            self._try_snapshot(price, total_long_usd, total_short_usd)
            return None

        # Check if price moved enough to resolve
        battle = self._check_resolved(price)
        if battle:
            self.db.log_battle(battle)
            self.stats = self.db.get_stats()
            self.recent_battles = self.db.get_recent_battles()
            self.snapshot = None
            self._try_snapshot(price, total_long_usd, total_short_usd)
        return battle

    def _try_snapshot(self, price: float, long_usd: float, short_usd: float) -> None:
        """Create a new snapshot if imbalance is large enough."""
        bigger = max(long_usd, short_usd)
        smaller = min(long_usd, short_usd)
        if smaller > 0 and bigger / smaller >= MIN_IMBALANCE_RATIO:
            self.snapshot = LiqSnapshot(
                btc_price=price,
                total_long_usd=long_usd,
                total_short_usd=short_usd,
                timestamp=time(),
            )

    def _check_resolved(self, price: float) -> Battle | None:
        """Check if price moved ±RESOLVE_MOVE_PCT from snapshot."""
        snap = self.snapshot
        move_pct = (price - snap.btc_price) / snap.btc_price * 100

        if abs(move_pct) < RESOLVE_MOVE_PCT:
            return None

        # Price dropped → moved toward LONG liquidations
        # Price rose → moved toward SHORT liquidations
        moved_side = "LONG" if move_pct < 0 else "SHORT"

        now = time()
        return Battle(
            bigger_side=snap.bigger_side,
            moved_side=moved_side,
            hypothesis_correct=(moved_side == snap.bigger_side),
            imbalance_ratio=snap.imbalance_ratio,
            duration_seconds=now - snap.timestamp,
            snapshot_price=snap.btc_price,
            resolved_price=price,
            move_pct=move_pct,
            total_long_usd=snap.total_long_usd,
            total_short_usd=snap.total_short_usd,
            timestamp=now,
        )
