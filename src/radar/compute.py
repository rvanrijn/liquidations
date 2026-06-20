"""Pure derived-input math, mirroring liqhunt/main.py. No I/O."""

from collections import deque

OI_VELOCITY_LOOKBACK = 12  # ~2 min at 10s cadence
PRICE_WINDOW_SEC = 21600   # 6h


def oi_change_pct(oi_history: deque) -> float:
    """OI % change from the rolling peak. 0.0 until 2+ samples."""
    if len(oi_history) < 2:
        return 0.0
    peak = max(oi_history)
    if peak <= 0:
        return 0.0
    return (oi_history[-1] - peak) / peak * 100


def oi_velocity(oi_history: deque) -> float:
    """OI velocity in %/min over the last ~2 min. 0.0 until 12+ samples."""
    if len(oi_history) < OI_VELOCITY_LOOKBACK:
        return 0.0
    prev = oi_history[-OI_VELOCITY_LOOKBACK]
    if prev <= 0:
        return 0.0
    return (oi_history[-1] - prev) / prev * 100 / 2


def cvd_sum(cvd_history: deque) -> float:
    """Running sum of recent 1m CVD deltas."""
    return float(sum(cvd_history))


def price_range_6h(price_history: deque, now: float) -> float:
    """High-low range over the trailing 6h. Mutates the deque (trims old)."""
    cutoff = now - PRICE_WINDOW_SEC
    while price_history and price_history[0][0] < cutoff:
        price_history.popleft()
    if len(price_history) < 2:
        return 0.0
    prices = [p for _, p in price_history]
    return max(prices) - min(prices)


def liq_side(binance_side: str) -> str:
    """Binance forceOrder side -> liquidated side. SELL=long liq, BUY=short liq."""
    return "long" if binance_side == "SELL" else "short"
