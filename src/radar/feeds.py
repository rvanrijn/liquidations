"""RadarLiqFeed — LiqFeed plus a per-event callback carrying price."""

from time import time
from typing import Callable, Optional

from src.liqhunt.liq_feed import LiqFeed
from src.radar.compute import liq_side


class RadarLiqFeed(LiqFeed):
    def __init__(self, symbol: str = "btcusdt",
                 on_event: Optional[Callable[[float, str, float, float], None]] = None):
        super().__init__(symbol)
        self._on_event = on_event

    def _on_message(self, data: dict) -> None:
        # Preserve base behavior (deque + totals).
        super()._on_message(data)
        if self._on_event is None:
            return
        o = data.get("o", {})
        side = o.get("S")
        qty = float(o.get("z", 0))
        avg_price = float(o.get("ap", 0))
        if qty <= 0 or avg_price <= 0:
            return
        self._on_event(time(), liq_side(side), qty * avg_price, avg_price)
