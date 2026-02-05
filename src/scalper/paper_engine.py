import logging
from datetime import datetime, timezone
from time import time as time_now

from src.scalper.models import Position, TradeRecord, BotState
from src.scalper.database import TradeDatabase

logger = logging.getLogger(__name__)


class PaperEngine:
    def __init__(self, db: TradeDatabase, initial_balance: float = 10_000.0):
        self.db = db
        self.state = BotState(
            account_balance=initial_balance,
            initial_balance=initial_balance,
        )
        self._last_reset_date = datetime.now(timezone.utc).date()

    def open_position(self, position: Position) -> None:
        """Open a new position. Updates state."""
        self.state.position = position
        logger.info(
            f"OPEN {position.side} @ ${position.entry_price:,.2f} | "
            f"Size: ${position.size_usd:,.0f} | Stop: ${position.stop_loss:,.2f}"
        )

    def close_position(
        self,
        exit_price: float,
        exit_reason: str,
        market_snapshot: dict,
    ) -> TradeRecord | None:
        """Close the current position.

        Args:
            exit_price: Price at which to close
            exit_reason: "TP1" | "TP2" | "STOP" | "EARLY_EXIT"
            market_snapshot: dict with keys: delta_5m, delta_15m, buy_pressure, vwap, long_liq_usd, short_liq_usd, bias

        For TP1: close 50% of position (set tp1_hit=True, remaining_pct=0.5, update state but don't remove position)
        For TP2/STOP/EARLY_EXIT: close remaining position entirely

        Calculate P&L:
        - For LONG: pnl = (exit_price - entry_price) / entry_price * size_usd * remaining_pct
        - For SHORT: pnl = (entry_price - exit_price) / entry_price * size_usd * remaining_pct

        After closing:
        - Update account_balance
        - Update daily_pnl
        - Increment daily_trades (only on full close, not TP1 partial)
        - Update wins/losses
        - Check if daily loss limit hit -> set is_active = False
        - Log trade to database

        Returns the TradeRecord (or None if no position).
        """
        if not self.state.position:
            logger.warning("No position to close")
            return None

        pos = self.state.position
        now_ms = int(time_now() * 1000)

        # Calculate P&L based on position side
        if pos.side == "LONG":
            pnl = (exit_price - pos.entry_price) / pos.entry_price * pos.size_usd * pos.remaining_pct
        else:  # SHORT
            pnl = (pos.entry_price - exit_price) / pos.entry_price * pos.size_usd * pos.remaining_pct

        # Calculate percentage and R-multiple
        pnl_pct = (pnl / (pos.size_usd * pos.remaining_pct)) * 100
        r_multiple = pnl / self.state.risk_usd if self.state.risk_usd > 0 else 0.0

        # Duration
        duration_s = int((now_ms - pos.entry_time) / 1000)

        # Determine if this is a partial close (TP1) or full close
        is_partial = exit_reason == "TP1" and not pos.tp1_hit

        # Update account balance and daily P&L
        self.state.account_balance += pnl
        self.state.daily_pnl += pnl

        # Create trade record
        trade = TradeRecord(
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            size_usd=pos.size_usd * pos.remaining_pct,
            leverage=pos.leverage,
            pnl_usd=pnl,
            pnl_percent=pnl_pct,
            r_multiple=r_multiple,
            entry_time=pos.entry_time,
            exit_time=now_ms,
            exit_reason=exit_reason,
            duration_seconds=duration_s,
            bias=market_snapshot.get("bias", "NONE"),
            delta_5m=market_snapshot.get("delta_5m", 0.0),
            delta_15m=market_snapshot.get("delta_15m", 0.0),
            buy_pressure=market_snapshot.get("buy_pressure", 0.0),
            vwap=market_snapshot.get("vwap", 0.0),
            long_liq_usd=market_snapshot.get("long_liq_usd", 0.0),
            short_liq_usd=market_snapshot.get("short_liq_usd", 0.0),
        )

        # Log trade to database
        self.db.log_trade(trade)

        # Handle partial close (TP1)
        if is_partial:
            pos.tp1_hit = True
            pos.remaining_pct = 0.5
            logger.info(
                f"TP1 HIT {pos.side} @ ${exit_price:,.2f} | "
                f"P&L: ${pnl:,.2f} ({pnl_pct:+.2f}%) | "
                f"50% closed, 50% remaining"
            )
        else:
            # Full close
            self.state.daily_trades += 1
            self.state.total_trades += 1

            if pnl > 0:
                self.state.wins += 1
            else:
                self.state.losses += 1

            logger.info(
                f"CLOSE {pos.side} @ ${exit_price:,.2f} | "
                f"P&L: ${pnl:,.2f} ({r_multiple:+.1f}R) | "
                f"Reason: {exit_reason}"
            )

            # Remove position
            self.state.position = None

            # Check daily loss limit (-2R)
            if self.state.risk_usd > 0:
                daily_r = self.state.daily_pnl / self.state.risk_usd
                if daily_r <= self.state.max_daily_loss_r:
                    self.state.is_active = False
                    logger.warning(
                        f"DAILY LOSS LIMIT HIT | "
                        f"Daily P&L: ${self.state.daily_pnl:,.2f} ({daily_r:.1f}R) | "
                        f"Bot deactivated until reset"
                    )

        return trade

    def check_daily_reset(self) -> None:
        """Reset daily counters at 00:00 UTC.

        Call this periodically. Check if current UTC date > last reset date.
        Reset: daily_pnl, daily_trades, is_active = True
        """
        current_date = datetime.now(timezone.utc).date()

        if current_date > self._last_reset_date:
            # New day - reset daily counters
            logger.info(
                f"DAILY RESET | "
                f"Previous day P&L: ${self.state.daily_pnl:,.2f} | "
                f"Trades: {self.state.daily_trades}"
            )

            self.state.daily_pnl = 0.0
            self.state.daily_trades = 0
            self.state.is_active = True
            self._last_reset_date = current_date

            logger.info("Daily counters reset. Bot reactivated.")

    def get_unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized P&L for open position."""
        if not self.state.position:
            return 0.0

        pos = self.state.position

        if pos.side == "LONG":
            unrealized = (current_price - pos.entry_price) / pos.entry_price * pos.size_usd * pos.remaining_pct
        else:  # SHORT
            unrealized = (pos.entry_price - current_price) / pos.entry_price * pos.size_usd * pos.remaining_pct

        return unrealized
