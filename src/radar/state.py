"""RadarState: the serializable contract between runner and frontend. No logic."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RadarState:
    price: float
    magnets: dict                       # {"long": [(price, usd)], "short": [...]}
    oi_delta_pct: float
    oi_velocity: float                  # %/min
    cvd_30m: float
    funding: float
    imbalance: Optional[float]          # None before first snapshot
    bigger_side: Optional[str]          # "LONG" | "SHORT" | None
    taker_ratio: float
    quality: float
    range_6h: float
    verdict: str                        # "ARMED" | "none"
    rejection_reason: str
    signal: Optional[dict]              # {direction, entry, stop, target} or None
    connections: dict                   # {"liq_feed": bool, "trade_feed": bool}
    dev_mode: bool = False              # True when engine gates relaxed for testing

    def to_dict(self) -> dict:
        return {
            "type": "state",
            "price": self.price,
            "magnets": {
                "long": [[p, u] for p, u in self.magnets.get("long", [])],
                "short": [[p, u] for p, u in self.magnets.get("short", [])],
            },
            "oi_delta_pct": self.oi_delta_pct,
            "oi_velocity": self.oi_velocity,
            "cvd_30m": self.cvd_30m,
            "funding": self.funding,
            "imbalance": self.imbalance,
            "bigger_side": self.bigger_side,
            "taker_ratio": self.taker_ratio,
            "quality": self.quality,
            "range_6h": self.range_6h,
            "verdict": self.verdict,
            "rejection_reason": self.rejection_reason,
            "signal": self.signal,
            "connections": self.connections,
            "dev_mode": self.dev_mode,
        }


def liq_message(ts: float, side: str, usd: float, price: float) -> dict:
    """Per-event liquidation message for the frontend blips."""
    return {"type": "liq", "ts": ts, "side": side, "usd": usd, "price": price}
