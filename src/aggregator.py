# src/aggregator.py
from collections import deque
import time
from src.models import LiquidationEvent

WINDOW_24H_MS = 24 * 60 * 60 * 1000
MIN_VALUE_USD = 1000


class LiquidationAggregator:
    def __init__(
        self,
        window_ms: int = WINDOW_24H_MS,
        min_value_usd: float = MIN_VALUE_USD,
    ):
        self.events: deque[LiquidationEvent] = deque()
        self.window_ms = window_ms
        self.min_value_usd = min_value_usd

    def add_event(self, event: LiquidationEvent) -> None:
        if event.value_usd < self.min_value_usd:
            return
        self.events.append(event)
        self._prune()

    def _prune(self) -> None:
        cutoff = int(time.time() * 1000) - self.window_ms
        while self.events and self.events[0].timestamp < cutoff:
            self.events.popleft()
