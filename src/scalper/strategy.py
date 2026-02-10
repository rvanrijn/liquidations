"""Strategy engine for BTC scalping bot.

All functions are pure (no side effects) for easy testing.
"""

from src.scalper.models import Bias, Position, BotState


def calculate_bias(
    long_liq_usd: float,
    short_liq_usd: float,
    delta_15m: float,
    price: float,
    vwap: float,
) -> Bias:
    """Calculate directional bias.

    SHORT: long_liq >= 2 * short_liq AND delta_15m < 0 AND price <= vwap
    LONG: short_liq >= 2 * long_liq AND delta_15m > 0 AND price >= vwap
    Otherwise: NONE

    Args:
        long_liq_usd: Total USD in long liquidations
        short_liq_usd: Total USD in short liquidations
        delta_15m: 15-minute cumulative delta
        price: Current BTC price
        vwap: Volume-weighted average price

    Returns:
        Bias object with direction and reason
    """
    price_vs_vwap = "ABOVE" if price >= vwap else "BELOW"

    # Check SHORT bias
    if long_liq_usd >= 2 * short_liq_usd and delta_15m < 0 and price <= vwap:
        return Bias(
            direction="SHORT",
            long_liq_usd=long_liq_usd,
            short_liq_usd=short_liq_usd,
            delta_15m=delta_15m,
            price_vs_vwap=price_vs_vwap,
        )

    # Check LONG bias
    if short_liq_usd >= 2 * long_liq_usd and delta_15m > 0 and price >= vwap:
        return Bias(
            direction="LONG",
            long_liq_usd=long_liq_usd,
            short_liq_usd=short_liq_usd,
            delta_15m=delta_15m,
            price_vs_vwap=price_vs_vwap,
        )

    # No bias
    return Bias(
        direction="NONE",
        long_liq_usd=long_liq_usd,
        short_liq_usd=short_liq_usd,
        delta_15m=delta_15m,
        price_vs_vwap=price_vs_vwap,
    )


def check_short_entry(
    bias: Bias,
    price: float,
    high_5m: float,
    delta_5m: float,
    buy_pressure: float,
    obv_macd_bearish: bool,
    state: BotState,
) -> bool:
    """Check if all SHORT entry conditions are met."""
    if bias.direction != "SHORT":
        return False
    if not obv_macd_bearish:
        return False
    if not (price >= high_5m * 0.997 and price <= high_5m * 0.999):
        return False
    if delta_5m >= 0:
        return False
    if buy_pressure >= 50.0:
        return False
    if state.position is not None:
        return False
    if not state.is_active:
        return False
    if state.daily_trades >= state.max_daily_trades:
        return False
    if state.daily_pnl / state.risk_usd <= state.max_daily_loss_r:
        return False
    return True


def check_long_entry(
    bias: Bias,
    price: float,
    low_5m: float,
    delta_5m: float,
    buy_pressure: float,
    obv_macd_bullish: bool,
    state: BotState,
) -> bool:
    """Check if all LONG entry conditions are met."""
    if bias.direction != "LONG":
        return False
    if not obv_macd_bullish:
        return False
    if not (price >= low_5m * 1.003 and price <= low_5m * 1.005):
        return False
    if delta_5m <= 0:
        return False
    if buy_pressure <= 50.0:
        return False
    if state.position is not None:
        return False
    if not state.is_active:
        return False
    if state.daily_trades >= state.max_daily_trades:
        return False
    if state.daily_pnl / state.risk_usd <= state.max_daily_loss_r:
        return False
    return True


def evaluate_conditions(
    bias: Bias,
    price: float,
    high_5m: float,
    low_5m: float,
    delta_5m: float,
    buy_pressure: float,
    obv_macd_bullish: bool,
    obv_macd_bearish: bool,
    obv_macd_value: str,
    state: BotState,
) -> tuple[list[tuple[str, bool, str]], list[tuple[str, bool, str]]]:
    """Evaluate all entry conditions for both sides.

    Returns:
        (short_conditions, long_conditions) where each is a list of
        (label, met, value_str) tuples.
    """
    daily_r = state.daily_pnl / state.risk_usd if state.risk_usd > 0 else 0.0

    short_conds = [
        ("Bias = SHORT", bias.direction == "SHORT", bias.direction),
        ("OBV MACD Bearish", obv_macd_bearish, obv_macd_value),
        ("Near 5m High", (price >= high_5m * 0.997 and price <= high_5m * 0.999) if high_5m > 0 else False,
         f"{(1 - price / high_5m) * 100:.2f}%" if high_5m > 0 else "N/A"),
        ("Delta 5m < 0", delta_5m < 0, f"${delta_5m:+,.0f}"),
        ("Buy Press < 50%", buy_pressure < 50.0, f"{buy_pressure:.1f}%"),
        ("No Open Position", state.position is None, "None" if state.position is None else state.position.side),
        ("Bot Active", state.is_active, "Yes" if state.is_active else "No"),
        ("Trades < Max", state.daily_trades < state.max_daily_trades, f"{state.daily_trades}/{state.max_daily_trades}"),
        ("Daily P&L > -2R", daily_r > state.max_daily_loss_r, f"{daily_r:+.1f}R"),
    ]

    long_conds = [
        ("Bias = LONG", bias.direction == "LONG", bias.direction),
        ("OBV MACD Bullish", obv_macd_bullish, obv_macd_value),
        ("Near 5m Low", (price >= low_5m * 1.003 and price <= low_5m * 1.005) if low_5m > 0 else False,
         f"{(price / low_5m - 1) * 100:.2f}%" if low_5m > 0 else "N/A"),
        ("Delta 5m > 0", delta_5m > 0, f"${delta_5m:+,.0f}"),
        ("Buy Press > 50%", buy_pressure > 50.0, f"{buy_pressure:.1f}%"),
        ("No Open Position", state.position is None, "None" if state.position is None else state.position.side),
        ("Bot Active", state.is_active, "Yes" if state.is_active else "No"),
        ("Trades < Max", state.daily_trades < state.max_daily_trades, f"{state.daily_trades}/{state.max_daily_trades}"),
        ("Daily P&L > -2R", daily_r > state.max_daily_loss_r, f"{daily_r:+.1f}R"),
    ]

    return short_conds, long_conds


def create_position(
    side: str,
    price: float,
    state: BotState,
    nearest_liq_price: float,
    reason: str = "",
) -> Position:
    """Create a new position with calculated stop, TP1, TP2.

    For SHORT:
        stop_loss = price * 1.0025 (+0.25%)
        tp1 = price * 0.990 (-1.0%)
        tp2 = nearest long liquidation zone (below price)

    For LONG:
        stop_loss = price * 0.9975 (-0.25%)
        tp1 = price * 1.010 (+1.0%)
        tp2 = nearest short liquidation zone (above price)

    Args:
        side: "LONG" or "SHORT"
        price: Entry price
        state: Current bot state
        nearest_liq_price: Nearest liquidation zone price
        reason: Entry reason for logging

    Returns:
        New Position object
    """
    if side == "SHORT":
        stop_loss = price * 1.0025  # +0.25%
        tp1 = price * 0.985  # -1.5%
        tp2 = nearest_liq_price  # Nearest long liq zone (below price)
    else:  # LONG
        stop_loss = price * 0.9975  # -0.25%
        tp1 = price * 1.015  # +1.5%
        tp2 = nearest_liq_price  # Nearest short liq zone (above price)

    return Position(
        side=side,
        entry_price=price,
        size_usd=state.position_size_usd,
        leverage=state.leverage,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        entry_reason=reason,
    )


def maybe_trail_stop(position: Position, current_price: float) -> None:
    """Move stop to breakeven once price moves 0.2% in our favor (before TP1).

    This converts some full -1R stops into ~0R scratches.
    """
    if position.tp1_hit:
        return  # Already at breakeven from TP1
    if position.side == "SHORT":
        if current_price <= position.entry_price * 0.998 and position.stop_loss > position.entry_price:
            position.stop_loss = position.entry_price
    else:  # LONG
        if current_price >= position.entry_price * 1.002 and position.stop_loss < position.entry_price:
            position.stop_loss = position.entry_price


def check_exit(
    position: Position,
    current_price: float,
    delta_5m: float,
    now_ms: int = 0,
    min_hold_ms: int = 15 * 60 * 1000,
) -> str | None:
    """Check if position should be exited.

    Returns exit reason or None:
    - "STOP": price hit stop loss (always checked)
    - "TP1": price hit TP1 (always checked)
    - "TP2": price hit TP2 (always checked)
    - "EARLY_EXIT": delta_5m flipped (only after min_hold_ms cooldown)
    - None: hold position

    Args:
        position: Current position
        current_price: Current BTC price
        delta_5m: 5-minute cumulative delta
        now_ms: Current time in epoch ms (0 = use wall clock)
        min_hold_ms: Minimum hold time before early exit allowed (default 15 min)

    Returns:
        Exit reason string or None to hold
    """
    if now_ms == 0:
        from time import time
        now_ms = int(time() * 1000)

    held_ms = now_ms - position.entry_time

    if position.side == "SHORT":
        # Check stop loss
        if current_price >= position.stop_loss:
            return "STOP"

        # Check TP1 (first time)
        if current_price <= position.tp1 and not position.tp1_hit:
            return "TP1"

        # Check TP2 (after TP1 hit)
        if current_price <= position.tp2 and position.tp1_hit:
            return "TP2"

        # Check early exit (delta flipped) — only after hold cooldown
        if held_ms >= min_hold_ms and delta_5m > 0:
            return "EARLY_EXIT"

    else:  # LONG
        # Check stop loss
        if current_price <= position.stop_loss:
            return "STOP"

        # Check TP1 (first time)
        if current_price >= position.tp1 and not position.tp1_hit:
            return "TP1"

        # Check TP2 (after TP1 hit)
        if current_price >= position.tp2 and position.tp1_hit:
            return "TP2"

        # Check early exit (delta flipped) — only after hold cooldown
        if held_ms >= min_hold_ms and delta_5m < 0:
            return "EARLY_EXIT"

    # Hold position
    return None
