# src/liqhunt/sweep.py
"""Sweep validation — checks whether a liquidation sweep occurred."""

from src.liqhunt.models import Candle, SweepResult

RANGE_MULTIPLIER = 1.2
WICK_MIN_RATIO = 0.4
ZONE_PCT = 0.004  # ±0.4% execution zone around magnet
MIN_CRITERIA = 3


def validate_sweep(
    candles: list[Candle],
    magnet_price: float,
    magnet_side: str,
    avg_range: float,
    liq_levels: list[float],
) -> SweepResult | None:
    """Validate whether a liquidation sweep occurred.

    Args:
        candles: Recent closed candles, newest last. Need at least 2.
        magnet_price: Current magnet price level.
        magnet_side: "LONG" (expect down sweep) or "SHORT" (expect up sweep).
        avg_range: Rolling average candle range.
        liq_levels: Known liquidation price levels on the magnet side.

    Returns:
        SweepResult if a sweep candle was found, None otherwise.
    """
    if len(candles) < 2 or avg_range <= 0:
        return None

    # Determine sweep direction from magnet side
    # LONG magnet = longs get liquidated = price drops = DOWN sweep
    sweep_down = magnet_side == "LONG"

    # Check the second-to-last candle as the sweep candle,
    # and the last candle as the continuation check
    sweep_candle = candles[-2]
    next_candle = candles[-1]

    # Did the sweep candle enter the execution zone?
    zone_low = magnet_price * (1 - ZONE_PCT)
    zone_high = magnet_price * (1 + ZONE_PCT)

    if sweep_down:
        if sweep_candle.low > zone_low:
            return None  # never reached the zone
        extreme = sweep_candle.low
    else:
        if sweep_candle.high < zone_high:
            return None  # never reached the zone
        extreme = sweep_candle.high

    # Evaluate the 5 criteria
    criteria = []

    # 1. Range test
    if sweep_candle.range >= avg_range * RANGE_MULTIPLIER:
        criteria.append("range")

    # 2. Wick test
    if sweep_candle.wick_ratio >= WICK_MIN_RATIO:
        criteria.append("wick")

    # 3. Close test — candle closes away from the extreme
    if sweep_down:
        if sweep_candle.close > sweep_candle.midpoint:
            criteria.append("close")
    else:
        if sweep_candle.close < sweep_candle.midpoint:
            criteria.append("close")

    # 4. Level breach — traded through at least one liq level
    for level in liq_levels:
        if sweep_down and sweep_candle.low <= level:
            criteria.append("level")
            break
        elif not sweep_down and sweep_candle.high >= level:
            criteria.append("level")
            break

    # 5. Continuation failure — next candle doesn't extend the sweep
    if sweep_down:
        if next_candle.low >= sweep_candle.low:
            criteria.append("no_cont")
    else:
        if next_candle.high <= sweep_candle.high:
            criteria.append("no_cont")

    direction = "DOWN" if sweep_down else "UP"

    return SweepResult(
        valid=len(criteria) >= MIN_CRITERIA,
        candle=sweep_candle,
        extreme=extreme,
        direction=direction,
        criteria_met=criteria,
    )
