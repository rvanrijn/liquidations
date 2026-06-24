# src/liqhunt/paper_trader.py
"""Paper trader — simulates Liquidation Storm trades with virtual balance."""

import logging
from dataclasses import dataclass
from time import time

from src.liqhunt.models import Signal
from src.liqlevels.models import Battle

logger = logging.getLogger(__name__)

STARTING_BALANCE = 5000.0
LEVERAGE = 5
MIN_HOLD_SEC = 10 * 60  # 10 min — don't allow early exits (OI flip/velocity) before this
MAX_HOLD_SEC = 60 * 60  # 60 min — close trade if still open after this
COOLDOWN_SEC = 50 * 60  # 50 min — wait after closing before new entries

OI_VEL_EXIT = 0.05  # exit when OI rising at >= +0.05%/min (cascade stalling)
OI_EMERGENCY_PCT = 0.3  # emergency exit: bypass hold time if OI spikes > +0.3% from entry
BREAKEVEN_TRIGGER_PCT = 0.20  # move stop to entry once trade reaches +0.20% profit


@dataclass
class PaperPosition:
    """An open paper trade."""

    direction: str  # "LONG" or "SHORT"
    entry_price: float
    entry_time: float
    notional: float  # balance * leverage
    stop_price: float = 0.0
    snapshot_price: float = 0.0  # monitor snapshot price — battle resolves at ±0.5%
    oi_at_entry: float = 0.0  # OI change % when signal fired
    oi_peak_during: float = 0.0  # highest OI change % seen during trade
    oi_usd_at_entry: float = 0.0  # raw OI USD value at entry for mid-trade comparison
    taker_ratio: float = 1.0  # taker buy/sell ratio at entry
    cvd_30m: float = 0.0  # CVD at entry
    breakeven_activated: bool = False  # True once trade reached +0.20% profit
    mfe_pct: float = 0.0  # max favorable excursion (best unrealized %)
    mae_pct: float = 0.0  # max adverse excursion (worst unrealized %)


@dataclass
class PaperTrade:
    """A completed paper trade."""

    direction: str
    entry_price: float
    exit_price: float
    entry_time: float
    exit_time: float
    pnl_usd: float
    balance_after: float
    move_pct: float
    oi_change_pct: float
    oi_at_entry: float
    oi_peak_during: float
    price_range_6h: float
    exit_reason: str  # "battle_resolved" or "snapshot_invalidated"
    taker_ratio: float = 1.0
    cvd_30m: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    id: int | None = None


class PaperTrader:
    """Simulates trades based on Liquidation Storm signals."""

    def __init__(self, db):
        self.db = db
        self.balance: float = db.get_paper_balance() or STARTING_BALANCE
        self.position: PaperPosition | None = db.load_open_position()
        self.recent_trades: list[PaperTrade] = db.get_recent_paper_trades()
        self.stats: dict = db.get_paper_stats()
        self.last_close_time: float = 0.0  # for cooldown
        # Restore cooldown from last trade if any
        if self.recent_trades:
            self.last_close_time = self.recent_trades[0].exit_time
        if self.position:
            logger.warning(
                "PAPER RESTORED %s @ %.0f | notional $%.0f",
                self.position.direction, self.position.entry_price, self.position.notional,
            )

    @property
    def skip_reason(self) -> str | None:
        """Return reason if entry is blocked, or None if allowed."""
        if self.position is not None:
            return "already in a trade"
        now = time()
        # Cooldown check
        if self.last_close_time > 0:
            elapsed = now - self.last_close_time
            if elapsed < COOLDOWN_SEC:
                remaining = int((COOLDOWN_SEC - elapsed) / 60)
                return f"cooldown: {remaining}min remaining"
        return None

    def on_signal(self, signal: Signal, snapshot_price: float = 0.0, oi_change_pct: float = 0.0, oi_usd: float = 0.0, taker_ratio: float = 1.0, cvd_30m: float = 0.0) -> None:
        """Called when signal engine fires. Opens a paper position."""
        reason = self.skip_reason
        if reason:
            if reason != "already in a trade":
                logger.info("PAPER SKIP: %s", reason)
            return

        notional = self.balance * LEVERAGE * signal.notional_scale
        self.position = PaperPosition(
            direction=signal.direction,
            entry_price=signal.entry_price,
            entry_time=time(),
            notional=notional,
            stop_price=signal.stop_price,
            snapshot_price=snapshot_price,
            oi_at_entry=oi_change_pct,
            oi_usd_at_entry=oi_usd,
            taker_ratio=taker_ratio,
            cvd_30m=cvd_30m,
        )
        self.db.save_open_position(self.position)
        logger.warning(
            "PAPER OPEN %s @ %.0f | notional $%.0f | balance $%.2f",
            signal.direction, signal.entry_price, notional, self.balance,
        )

    def update_oi(self, oi_change_pct: float, current_oi_usd: float = 0.0) -> None:
        """Track peak OI during open trade."""
        if self.position is not None:
            if current_oi_usd > 0 and self.position.oi_usd_at_entry > 0:
                oi_from_entry = (current_oi_usd - self.position.oi_usd_at_entry) / self.position.oi_usd_at_entry * 100
                if oi_from_entry > self.position.oi_peak_during:
                    self.position.oi_peak_during = oi_from_entry
            elif oi_change_pct > self.position.oi_peak_during:
                self.position.oi_peak_during = oi_change_pct

    def update_breakeven(self, current_price: float) -> None:
        """Move stop to entry price once trade reaches +0.20% profit. Also tracks MFE/MAE."""
        if self.position is None:
            return
        pos = self.position
        if pos.direction == "LONG":
            unrealized_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        else:
            unrealized_pct = (pos.entry_price - current_price) / pos.entry_price * 100
        # Track MFE/MAE
        if unrealized_pct > pos.mfe_pct:
            pos.mfe_pct = unrealized_pct
        if unrealized_pct < pos.mae_pct:
            pos.mae_pct = unrealized_pct
        if not pos.breakeven_activated and unrealized_pct >= BREAKEVEN_TRIGGER_PCT:
            pos.breakeven_activated = True
            pos.stop_price = pos.entry_price
            logger.warning(
                "BREAKEVEN ACTIVATED: %s @ %.0f now %.0f (+%.2f%%) | stop → %.0f",
                pos.direction, pos.entry_price, current_price, unrealized_pct, pos.entry_price,
            )

    def check_stop_loss(self, current_price: float, price_range_6h: float = 0.0) -> PaperTrade | None:
        """Exit if price crosses the stop level."""
        if self.position is None or self.position.stop_price <= 0:
            return None
        pos = self.position
        stopped = (
            (pos.direction == "LONG" and current_price <= pos.stop_price)
            or (pos.direction == "SHORT" and current_price >= pos.stop_price)
        )
        if stopped:
            logger.warning(
                "STOP LOSS: %s @ %.0f → stop %.0f hit (price %.0f)",
                pos.direction, pos.entry_price, pos.stop_price, current_price,
            )
            return self._close(pos.stop_price, 0.0, price_range_6h, "stop_loss")
        return None

    def check_oi_exit(self, current_oi_usd: float, current_price: float, price_range_6h: float = 0.0) -> PaperTrade | None:
        """Exit early if OI rises above entry level (liquidations stopped, new positions opening)."""
        if self.position is None or self.position.oi_usd_at_entry <= 0 or current_oi_usd <= 0:
            return None
        # Compare current OI directly to entry OI
        oi_change_from_entry = (current_oi_usd - self.position.oi_usd_at_entry) / self.position.oi_usd_at_entry * 100
        held = time() - self.position.entry_time
        # Emergency exit: bypass hold time if OI spikes hard (> +0.3% from entry)
        if held < MIN_HOLD_SEC and oi_change_from_entry >= OI_EMERGENCY_PCT:
            logger.warning(
                "EMERGENCY OI EXIT: OI +%.2f%% from entry (>%.1f%%) after only %.0fs | closing %s @ %.0f",
                oi_change_from_entry, OI_EMERGENCY_PCT, held,
                self.position.direction, current_price,
            )
            return self._close(current_price, oi_change_from_entry, price_range_6h, "oi_emergency")
        # Normal hold time gate
        if held < MIN_HOLD_SEC:
            return None
        if oi_change_from_entry > 0:
            logger.warning(
                "OI FLIP EXIT: OI at entry $%.0fB → now $%.0fB (%+.2f%%) | closing %s @ %.0f",
                self.position.oi_usd_at_entry / 1e9, current_oi_usd / 1e9, oi_change_from_entry,
                self.position.direction, current_price,
            )
            return self._close(current_price, oi_change_from_entry, price_range_6h, "oi_flip")
        return None

    def check_oi_velocity_exit(self, oi_velocity: float, current_price: float, price_range_6h: float = 0.0) -> PaperTrade | None:
        """Exit early if OI velocity turns positive (cascade stalling, new positions opening)."""
        if self.position is None:
            return None
        held = time() - self.position.entry_time
        if held < MIN_HOLD_SEC:
            return None
        if oi_velocity >= OI_VEL_EXIT:
            logger.warning(
                "OI VELOCITY EXIT: vel %+.3f%%/min >= +%.3f%%/min | closing %s @ %.0f after %.0fmin",
                oi_velocity, OI_VEL_EXIT,
                self.position.direction, current_price, held / 60,
            )
            return self._close(current_price, oi_velocity, price_range_6h, "oi_velocity_exit")
        return None

    def check_max_hold(self, current_price: float, price_range_6h: float = 0.0) -> PaperTrade | None:
        """Exit if position has been held longer than MAX_HOLD_SEC."""
        if self.position is None:
            return None
        held = time() - self.position.entry_time
        if held >= MAX_HOLD_SEC:
            logger.warning(
                "MAX HOLD EXIT: %s @ %.0f held %.0fmin (max %dmin) | closing @ %.0f",
                self.position.direction, self.position.entry_price,
                held / 60, MAX_HOLD_SEC // 60, current_price,
            )
            return self._close(current_price, 0.0, price_range_6h, "max_hold")
        return None

    def on_battle_resolved(self, battle: Battle, price_range_6h: float = 0.0) -> PaperTrade | None:
        """Called when a battle resolves. Closes paper position if open."""
        if self.position is None:
            return None
        return self._close(battle.resolved_price, battle.oi_change_pct, price_range_6h, "battle_resolved")

    def on_snapshot_invalidated(self, current_price: float, price_range_6h: float = 0.0) -> PaperTrade | None:
        """Called when snapshot flips sides. Closes paper position at current price."""
        if self.position is None:
            return None
        return self._close(current_price, 0.0, price_range_6h, "snapshot_invalidated")

    def _close(self, exit_price: float, oi_change_pct: float, price_range_6h: float, reason: str) -> PaperTrade:
        pos = self.position

        if pos.direction == "LONG":
            move_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            move_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        pnl = move_pct / 100 * pos.notional
        self.balance += pnl

        trade = PaperTrade(
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=time(),
            pnl_usd=pnl,
            balance_after=self.balance,
            move_pct=move_pct,
            oi_change_pct=oi_change_pct,
            oi_at_entry=pos.oi_at_entry,
            oi_peak_during=pos.oi_peak_during,
            price_range_6h=price_range_6h,
            exit_reason=reason,
            taker_ratio=pos.taker_ratio,
            cvd_30m=pos.cvd_30m,
            mfe_pct=pos.mfe_pct,
            mae_pct=pos.mae_pct,
        )

        self.db.log_paper_trade(trade)
        self.db.clear_open_position()
        self.recent_trades = self.db.get_recent_paper_trades()
        self.stats = self.db.get_paper_stats()
        self.position = None
        self.last_close_time = time()

        logger.warning(
            "PAPER CLOSE %s @ %.0f | PnL %+.2f | Balance $%.2f | %s",
            trade.direction, exit_price, pnl, self.balance, reason,
        )
        return trade