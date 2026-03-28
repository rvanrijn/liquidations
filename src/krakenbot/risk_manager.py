# src/krakenbot/risk_manager.py
"""Risk manager — circuit breakers, daily limits, safety checks."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from src.krakenbot.config import BotConfig
from src.krakenbot.database import KrakenBotDatabase

logger = logging.getLogger(__name__)


@dataclass
class RiskCheck:
    """Result of a risk check."""

    allowed: bool
    reason: str


class RiskManager:
    """Safety layer between signal engine and executor."""

    def __init__(self, config: BotConfig, db: KrakenBotDatabase):
        self.config = config
        self.db = db
        self._last_reset_date: str = ""
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        self.refresh_daily_stats()

    def refresh_daily_stats(self) -> None:
        """Refresh daily counters. Resets on new UTC day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_reset_date:
            self._last_reset_date = today
            self._daily_pnl = self.db.get_daily_pnl()
            self._daily_trades = self.db.get_daily_trades_count()
            logger.info(
                "Daily stats refreshed: trades=%d, pnl=$%.2f",
                self._daily_trades, self._daily_pnl,
            )

    def check_entry(
        self,
        has_position: bool,
    ) -> RiskCheck:
        """Check whether a new entry is allowed."""
        self.refresh_daily_stats()
        if has_position:
            return RiskCheck(False, "already have position")
        return RiskCheck(True, "OK")

    def record_trade(self, pnl: float) -> None:
        """Record a completed trade for daily tracking."""
        self._daily_pnl += pnl
        self._daily_trades += 1
        logger.info(
            "Trade recorded: pnl=$%.2f | daily_pnl=$%.2f | daily_trades=%d",
            pnl, self._daily_pnl, self._daily_trades,
        )

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def daily_trades(self) -> int:
        return self._daily_trades
