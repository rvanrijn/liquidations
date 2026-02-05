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
    has_large_buys_30s: bool,
    state: BotState,
) -> bool:
    """Check if all SHORT entry conditions are met.

    1. bias.direction == "SHORT"
    2. price within 0.1-0.3% of high_5m (i.e., price >= high_5m * 0.997 and price <= high_5m * 0.999)
    3. delta_5m < 0
    4. buy_pressure < 50.0
    5. has_large_buys_30s is False
    6. state.position is None
    7. state.is_active is True
    8. state.daily_trades < state.max_daily_trades
    9. state.daily_pnl / state.risk_usd > state.max_daily_loss_r (not blown daily loss)

    Args:
        bias: Current directional bias
        price: Current BTC price
        high_5m: 5-minute high
        delta_5m: 5-minute cumulative delta
        buy_pressure: Buy pressure percentage
        has_large_buys_30s: True if large buys detected in last 30s
        state: Current bot state

    Returns:
        True if all conditions met, False otherwise
    """
    # 1. Check bias
    if bias.direction != "SHORT":
        return False

    # 2. Price within 0.1-0.3% of high_5m
    if not (price >= high_5m * 0.997 and price <= high_5m * 0.999):
        return False

    # 3. Negative delta
    if delta_5m >= 0:
        return False

    # 4. Low buy pressure
    if buy_pressure >= 50.0:
        return False

    # 5. No large buys
    if has_large_buys_30s:
        return False

    # 6. No existing position
    if state.position is not None:
        return False

    # 7. Bot is active
    if not state.is_active:
        return False

    # 8. Daily trades limit
    if state.daily_trades >= state.max_daily_trades:
        return False

    # 9. Daily loss limit
    if state.daily_pnl / state.risk_usd <= state.max_daily_loss_r:
        return False

    return True


def check_long_entry(
    bias: Bias,
    price: float,
    low_5m: float,
    delta_5m: float,
    buy_pressure: float,
    has_large_sells_30s: bool,
    state: BotState,
) -> bool:
    """Check if all LONG entry conditions are met.

    1. bias.direction == "LONG"
    2. price within 0.3-0.5% of low_5m (i.e., price >= low_5m * 1.003 and price <= low_5m * 1.005)
    3. delta_5m > 0 (strongly positive)
    4. buy_pressure > 50.0
    5. has_large_sells_30s is False
    6. state.position is None
    7. state.is_active is True
    8. state.daily_trades < state.max_daily_trades
    9. state.daily_pnl / state.risk_usd > state.max_daily_loss_r

    Args:
        bias: Current directional bias
        price: Current BTC price
        low_5m: 5-minute low
        delta_5m: 5-minute cumulative delta
        buy_pressure: Buy pressure percentage
        has_large_sells_30s: True if large sells detected in last 30s
        state: Current bot state

    Returns:
        True if all conditions met, False otherwise
    """
    # 1. Check bias
    if bias.direction != "LONG":
        return False

    # 2. Price within 0.3-0.5% of low_5m
    if not (price >= low_5m * 1.003 and price <= low_5m * 1.005):
        return False

    # 3. Positive delta
    if delta_5m <= 0:
        return False

    # 4. High buy pressure
    if buy_pressure <= 50.0:
        return False

    # 5. No large sells
    if has_large_sells_30s:
        return False

    # 6. No existing position
    if state.position is not None:
        return False

    # 7. Bot is active
    if not state.is_active:
        return False

    # 8. Daily trades limit
    if state.daily_trades >= state.max_daily_trades:
        return False

    # 9. Daily loss limit
    if state.daily_pnl / state.risk_usd <= state.max_daily_loss_r:
        return False

    return True


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
        tp1 = price * 0.997 (-0.3%)
        tp2 = nearest long liquidation zone (below price)

    For LONG:
        stop_loss = price * 0.9975 (-0.25%)
        tp1 = price * 1.003 (+0.3%)
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
        tp1 = price * 0.997  # -0.3%
        tp2 = nearest_liq_price  # Nearest long liq zone (below price)
    else:  # LONG
        stop_loss = price * 0.9975  # -0.25%
        tp1 = price * 1.003  # +0.3%
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


def check_exit(
    position: Position,
    current_price: float,
    delta_5m: float,
) -> str | None:
    """Check if position should be exited.

    Returns exit reason or None:
    - "STOP": price hit stop loss
    - "TP1": price hit TP1 (first time, partial exit)
    - "TP2": price hit TP2 (after TP1 already hit)
    - "EARLY_EXIT": delta_5m flipped against position
    - None: hold position

    For SHORT:
        STOP if current_price >= stop_loss
        TP1 if current_price <= tp1 and not tp1_hit
        TP2 if current_price <= tp2 and tp1_hit
        EARLY_EXIT if delta_5m > 0

    For LONG:
        STOP if current_price <= stop_loss
        TP1 if current_price >= tp1 and not tp1_hit
        TP2 if current_price >= tp2 and tp1_hit
        EARLY_EXIT if delta_5m < 0

    Args:
        position: Current position
        current_price: Current BTC price
        delta_5m: 5-minute cumulative delta

    Returns:
        Exit reason string or None to hold
    """
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

        # Check early exit (delta flipped)
        if delta_5m > 0:
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

        # Check early exit (delta flipped)
        if delta_5m < 0:
            return "EARLY_EXIT"

    # Hold position
    return None
