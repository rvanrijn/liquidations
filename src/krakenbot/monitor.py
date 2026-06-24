# src/krakenbot/monitor.py
"""Magnet monitor — tracks whether price moves toward the bigger liquidation side first."""

import logging
from time import time

from src.krakenbot.liq_client import CoinLiquidations
from src.krakenbot.database import KrakenBotDatabase
from src.krakenbot.liq_models import Battle, LiqSnapshot, RESOLVE_MOVE_PCT

logger = logging.getLogger(__name__)

MIN_IMBALANCE_RATIO = 1.1


class MagnetMonitor:
    """Core engine: snapshot imbalance, track price direction, resolve battles."""

    def __init__(self, db: KrakenBotDatabase):
        self.db = db
        self.snapshot: LiqSnapshot | None = None
        self.stats = db.get_stats()
        self.recent_battles = db.get_recent_battles()
        self.price_range_6h: float = db.get_price_range_6h()
        self._battle_mfe: float = 0.0  # best move toward magnet during active battle
        self._battle_mae: float = 0.0  # worst move against magnet during active battle

    def update(self, btc_data: CoinLiquidations, ha_color: str = "", ha_body_ratio: float = 0.0, ha_streak: int = 0) -> Battle | None:
        """Called every poll with fresh BTC data. Returns a Battle if one was just resolved."""
        price = btc_data.current_price
        current_oi = btc_data.open_interest_usd
        funding_rate = btc_data.funding_rate

        total_long_usd = sum(l.estimated_usd for l in btc_data.longs_at_risk)
        total_short_usd = sum(l.estimated_usd for l in btc_data.shorts_at_risk)

        if self.snapshot is None:
            self._try_snapshot(price, total_long_usd, total_short_usd, current_oi, funding_rate)
            return None

        current_bigger = "LONG" if total_long_usd >= total_short_usd else "SHORT"
        if current_bigger != self.snapshot.bigger_side:
            logger.debug("Imbalance flipped from %s to %s, resetting",
                         self.snapshot.bigger_side, current_bigger)
            self.snapshot = None
            self._battle_mfe = 0.0
            self._battle_mae = 0.0
            self._try_snapshot(price, total_long_usd, total_short_usd, current_oi, funding_rate)
            return None

        # Track MFE/MAE: favorable = move toward magnet side, adverse = move away
        # LONG magnet → price should drop (negative move_pct is favorable)
        # SHORT magnet → price should rise (positive move_pct is favorable)
        _move = (price - self.snapshot.btc_price) / self.snapshot.btc_price * 100
        if self.snapshot.bigger_side == "LONG":
            _favorable = -_move  # dropping is good for LONG magnet
        else:
            _favorable = _move   # rising is good for SHORT magnet
        if _favorable > self._battle_mfe:
            self._battle_mfe = _favorable
        if _favorable < self._battle_mae:
            self._battle_mae = _favorable

        battle = self._check_resolved(price, current_oi, ha_color, ha_body_ratio, ha_streak)
        if battle:
            battle.mfe_pct = self._battle_mfe
            battle.mae_pct = self._battle_mae
            self.db.log_battle(battle)
            self.stats = self.db.get_stats()
            self.recent_battles = self.db.get_recent_battles()
            self.price_range_6h = self.db.get_price_range_6h()
            self.snapshot = None
            self._battle_mfe = 0.0
            self._battle_mae = 0.0
            self._try_snapshot(price, total_long_usd, total_short_usd, current_oi, funding_rate)
        return battle

    def _try_snapshot(self, price: float, long_usd: float, short_usd: float, oi_usd: float = 0.0, funding_rate: float = 0.0) -> None:
        bigger = max(long_usd, short_usd)
        smaller = min(long_usd, short_usd)
        if smaller > 0 and bigger / smaller >= MIN_IMBALANCE_RATIO:
            self.snapshot = LiqSnapshot(
                btc_price=price,
                total_long_usd=long_usd,
                total_short_usd=short_usd,
                timestamp=time(),
                open_interest_usd=oi_usd,
                funding_rate=funding_rate,
            )

    def _check_resolved(self, price: float, current_oi: float = 0.0, ha_color: str = "", ha_body_ratio: float = 0.0, ha_streak: int = 0) -> Battle | None:
        snap = self.snapshot
        move_pct = (price - snap.btc_price) / snap.btc_price * 100

        if abs(move_pct) < RESOLVE_MOVE_PCT:
            return None

        moved_side = "LONG" if move_pct < 0 else "SHORT"

        oi_change_pct = 0.0
        if snap.open_interest_usd > 0 and current_oi > 0:
            oi_change_pct = (current_oi - snap.open_interest_usd) / snap.open_interest_usd * 100

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
            oi_start_usd=snap.open_interest_usd,
            oi_end_usd=current_oi,
            oi_change_pct=oi_change_pct,
            funding_rate=snap.funding_rate,
            ha_color=ha_color,
            ha_body_ratio=ha_body_ratio,
            ha_streak=ha_streak,
        )
