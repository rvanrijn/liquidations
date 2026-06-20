"""Pure assembly of RadarState from raw engine inputs + a Signal. No I/O."""

from typing import Optional

from src.liqhunt.models import Signal
from src.liqlevels.models import LiqSnapshot
from src.radar.state import RadarState


def build_state(
    *,
    snapshot: Optional[LiqSnapshot],
    signal: Optional[Signal],
    rejection_reason: str,
    price: float,
    oi_delta_pct: float,
    oi_velocity: float,
    cvd_30m: float,
    funding: float,
    taker_ratio: float,
    quality: float,
    range_6h: float,
    liq_levels_long: list,
    liq_levels_short: list,
    connections: dict,
) -> RadarState:
    if signal is not None:
        verdict = "ARMED"
        sig_dict = {
            "direction": signal.direction,
            "entry": signal.entry_price,
            "stop": signal.stop_price,
            "target": signal.primary_target,
        }
    else:
        verdict = "none"
        sig_dict = None

    return RadarState(
        price=price,
        magnets={"long": list(liq_levels_long), "short": list(liq_levels_short)},
        oi_delta_pct=oi_delta_pct,
        oi_velocity=oi_velocity,
        cvd_30m=cvd_30m,
        funding=funding,
        imbalance=snapshot.imbalance_ratio if snapshot else None,
        bigger_side=snapshot.bigger_side if snapshot else None,
        taker_ratio=taker_ratio,
        quality=quality,
        range_6h=range_6h,
        verdict=verdict,
        rejection_reason=rejection_reason,
        signal=sig_dict,
        connections=connections,
    )
