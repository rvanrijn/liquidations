# src/liqhunt/battle_trader.py
"""Battle trader — parallel paper trader using OI gate only (no signal engine)."""

import logging
from datetime import datetime, timezone
from time import time

from src.liqhunt.paper_trader import PaperPosition, PaperTrade
from src.liqlevels.models import Battle

logger = logging.getLogger(__name__)

STARTING_BALANCE = 5000.0
LEVERAGE = 5
MIN_HOLD_SEC = 4 * 60  # 4 min — don't allow early exits before this

OI_GATE_PCT = -0.10  # loosened from -0.35 (CVD gate compensates: OI -0.10 + CVD aligned = 94% WR)
MIN_IMBALANCE = 1.25  # minimum imbalance ratio to enter
SKIP_HOURS = {7, 8, 15, 18}  # session opens: 29-41% WR in 748-battle dataset
OI_EMERGENCY_PCT = 0.3  # emergency exit: bypass hold time if OI spikes > +0.3%
OI_EMERGENCY_MIN_SEC = 30  # 30s grace period to avoid single-tick noise
OI_DEEP_THRESHOLD = -0.45  # OI this deep = bounce risk, set breakeven stop
HA_MIN_BODY_RATIO = 0.3  # filter only garbage HA candles (0.6 was too strict, blocked winners)
HA_MIN_STREAK = 2  # skip flips (first candle of new color), require 2+ confirmation
MAKER_FEE_PCT = 0.02  # Kraken maker fee (limit entry)
TAKER_FEE_PCT = 0.05  # Kraken taker fee (market exit)
COOLDOWN_SEC = 20 * 60  # 20 min cooldown between trades (re-entry <20m = 41% WR, >20m = 71% WR)


class BattleTrader:
    """Parallel paper trader — enters on OI gate + imbalance only, bypasses signal engine."""

    def __init__(self, db):
        self.db = db
        self.balance: float = db.get_battle_balance() or STARTING_BALANCE
        self.position: PaperPosition | None = db.load_battle_position()
        self.recent_trades: list[PaperTrade] = db.get_recent_battle_trades()
        self.stats: dict = db.get_battle_stats()
        self.last_trade_time: float = self.recent_trades[-1].exit_time if self.recent_trades else 0.0
        if self.position:
            logger.warning(
                "BATTLE RESTORED %s @ %.0f | notional $%.0f",
                self.position.direction, self.position.entry_price, self.position.notional,
            )

    @property
    def skip_reason(self) -> str | None:
        """Return reason if entry is blocked, or None if allowed."""
        if self.position is not None:
            return "already in a trade"
        return None

    def check_entry(self, snapshot, oi_change_pct: float, btc_price: float, oi_usd: float,
                     ha_signal: tuple[str, float, int] = ("", 0.0, 0),
                     taker_ratio: float = 1.0) -> None:
        """Enter if snapshot active + OI gate + imbalance gate + HA quality + CVD."""
        if self.position or self.skip_reason:
            return
        if self.last_trade_time > 0 and (time() - self.last_trade_time) < COOLDOWN_SEC:
            return
        if snapshot is None or snapshot.imbalance_ratio < MIN_IMBALANCE:
            return
        if datetime.now(timezone.utc).hour in SKIP_HOURS:
            return
        if oi_change_pct > OI_GATE_PCT:
            return

        # Direction: LONG magnet = SHORT trade, SHORT magnet = LONG trade
        direction = "SHORT" if snapshot.bigger_side == "LONG" else "LONG"

        # CVD contrarian gate: buy pressure for SHORT (longs liquidating), sell pressure for LONG
        cvd_aligned = (direction == "SHORT" and taker_ratio < 1.0) or \
                      (direction == "LONG" and taker_ratio > 1.0)
        if not cvd_aligned:
            logger.info("BATTLE SKIP: CVD misaligned — %s wants TR %s 1.0 but got %.2f",
                        direction, "<" if direction == "SHORT" else ">", taker_ratio)
            return

        # HA 3m filter: color alignment + body strength + streak
        ha_color, ha_body_ratio, ha_streak = ha_signal
        logger.warning("BATTLE HA CHECK: %s | HA=%s br=%.2f streak=%d | signal=%s",
                       direction, ha_color, ha_body_ratio, ha_streak, ha_signal)
        if not ha_color:
            logger.info("BATTLE SKIP: HA data unavailable (cold start or fetch error)")
            return
        aligned = (direction == "SHORT" and ha_color == "RED") or \
                  (direction == "LONG" and ha_color == "GREEN")
        if not aligned:
            logger.info("BATTLE SKIP: %s wants %s HA but got %s", direction,
                        "RED" if direction == "SHORT" else "GREEN", ha_color)
            return
        if ha_streak < HA_MIN_STREAK:
            logger.info("BATTLE SKIP: HA streak %d < %d (flip, wait for confirmation)",
                        ha_streak, HA_MIN_STREAK)
            return
        if ha_body_ratio < HA_MIN_BODY_RATIO:
            logger.info("BATTLE SKIP: HA body ratio %.2f < %.1f (weak candle)",
                        ha_body_ratio, HA_MIN_BODY_RATIO)
            return

        notional = self.balance * LEVERAGE
        self.position = PaperPosition(
            direction=direction,
            entry_price=snapshot.btc_price,  # enter at snapshot price (simulates limit order)
            entry_time=time(),
            notional=notional,
            stop_price=0.0,  # no stop — ride to battle resolution
            snapshot_price=snapshot.btc_price,
            oi_at_entry=oi_change_pct,
            oi_usd_at_entry=oi_usd,
        )
        self.db.save_battle_position(self.position)
        logger.warning(
            "BATTLE OPEN %s @ %.0f | OI %.2f%% | imb %.1fx | notional $%.0f | balance $%.2f",
            direction, btc_price, oi_change_pct, snapshot.imbalance_ratio, notional, self.balance,
        )

    # --- Exit methods (identical to PaperTrader) ---

    def update_oi(self, oi_change_pct: float, current_oi_usd: float = 0.0) -> None:
        """Track peak OI during open trade."""
        if self.position is not None:
            if current_oi_usd > 0 and self.position.oi_usd_at_entry > 0:
                oi_from_entry = (current_oi_usd - self.position.oi_usd_at_entry) / self.position.oi_usd_at_entry * 100
                if oi_from_entry > self.position.oi_peak_during:
                    self.position.oi_peak_during = oi_from_entry
            elif oi_change_pct > self.position.oi_peak_during:
                self.position.oi_peak_during = oi_change_pct

    def update_mfe_mae(self, current_price: float) -> None:
        """Track MFE/MAE for the open position."""
        if self.position is None:
            return
        pos = self.position
        if pos.direction == "LONG":
            unrealized_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        else:
            unrealized_pct = (pos.entry_price - current_price) / pos.entry_price * 100
        if unrealized_pct > pos.mfe_pct:
            pos.mfe_pct = unrealized_pct
        if unrealized_pct < pos.mae_pct:
            pos.mae_pct = unrealized_pct

    def check_breakeven_stop(self, current_price: float, price_range_6h: float = 0.0) -> PaperTrade | None:
        """Breakeven stop when OI was deep at entry — bounce risk protection."""
        if self.position is None:
            return None
        if self.position.oi_at_entry > OI_DEEP_THRESHOLD:
            return None  # OI wasn't deep enough to warrant BE stop
        # Check if price has returned to entry
        pos = self.position
        if (pos.direction == "SHORT" and current_price >= pos.entry_price) or \
           (pos.direction == "LONG" and current_price <= pos.entry_price):
            logger.warning(
                "BATTLE BREAKEVEN STOP: OI was %.2f%% at entry (deep) — price returned to entry %.0f | closing %s",
                pos.oi_at_entry, pos.entry_price, pos.direction,
            )
            return self._close(pos.entry_price, 0.0, price_range_6h, "breakeven_stop")
        return None

    def check_oi_exit(self, current_oi_usd: float, current_price: float, price_range_6h: float = 0.0, taker_ratio: float = 1.0) -> PaperTrade | None:
        """Exit early if OI rises above entry level."""
        if self.position is None or self.position.oi_usd_at_entry <= 0 or current_oi_usd <= 0:
            return None
        oi_change_from_entry = (current_oi_usd - self.position.oi_usd_at_entry) / self.position.oi_usd_at_entry * 100
        held = time() - self.position.entry_time
        # Emergency exit: bypass hold time if OI spikes hard — but only if CVD confirms reversal
        if held >= OI_EMERGENCY_MIN_SEC and held < MIN_HOLD_SEC and oi_change_from_entry >= OI_EMERGENCY_PCT:
            cvd_confirms = (
                (self.position.direction == "SHORT" and taker_ratio > 1.0)
                or (self.position.direction == "LONG" and taker_ratio < 1.0)
            )
            if cvd_confirms:
                logger.warning(
                    "BATTLE EMERGENCY OI EXIT: OI +%.2f%% from entry (>%.1f%%) after %.0fs | taker %.2f | closing %s @ %.0f",
                    oi_change_from_entry, OI_EMERGENCY_PCT, held, taker_ratio,
                    self.position.direction, current_price,
                )
                return self._close(current_price, oi_change_from_entry, price_range_6h, "oi_emergency")
            else:
                logger.warning(
                    "BATTLE EMERGENCY OI BLOCKED by CVD: OI +%.2f%% but taker %.2f favors %s — holding",
                    oi_change_from_entry, taker_ratio, self.position.direction,
                )
        return None

    def on_battle_resolved(self, battle: Battle, price_range_6h: float = 0.0) -> PaperTrade | None:
        """Called when a battle resolves. Closes position if open."""
        if self.position is None:
            return None
        return self._close(battle.resolved_price, battle.oi_change_pct, price_range_6h, "battle_resolved")

    def on_snapshot_invalidated(self, current_price: float, price_range_6h: float = 0.0) -> PaperTrade | None:
        """Called when snapshot flips sides. Closes position at current price."""
        if self.position is None:
            return None
        return self._close(current_price, 0.0, price_range_6h, "snapshot_invalidated")

    def _close(self, exit_price: float, oi_change_pct: float, price_range_6h: float, reason: str) -> PaperTrade:
        pos = self.position

        if pos.direction == "LONG":
            move_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            move_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        fee = pos.notional * (MAKER_FEE_PCT + TAKER_FEE_PCT) / 100  # limit entry + market exit
        pnl = move_pct / 100 * pos.notional - fee
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

        self.db.log_battle_trade(trade)
        self.db.clear_battle_position()
        self.recent_trades = self.db.get_recent_battle_trades()
        self.stats = self.db.get_battle_stats()
        self.position = None
        self.last_trade_time = time()
        logger.warning(
            "BATTLE CLOSE %s @ %.0f | PnL %+.2f | Balance $%.2f | %s | cooldown %dmin",
            trade.direction, exit_price, pnl, self.balance, reason, COOLDOWN_SEC // 60,
        )
        return trade
