from collections import deque, defaultdict
import time
from typing import TypedDict
from src.models import LiquidationEvent

WINDOW_24H_MS = 24 * 60 * 60 * 1000
MIN_VALUE_USD = 1000


class CoinStats(TypedDict):
    count: int
    total_usd: float
    long_usd: float
    short_usd: float


class ExchangeStats(TypedDict):
    count: int
    total_usd: float


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

    def top_10(self) -> list[LiquidationEvent]:
        return sorted(self.events, key=lambda e: e.value_usd, reverse=True)[:10]

    def by_coin(self) -> dict[str, CoinStats]:
        stats: dict[str, CoinStats] = defaultdict(
            lambda: {"count": 0, "total_usd": 0.0, "long_usd": 0.0, "short_usd": 0.0}
        )
        for e in self.events:
            s = stats[e.coin]
            s["count"] += 1
            s["total_usd"] += e.value_usd
            if e.side == "long":
                s["long_usd"] += e.value_usd
            else:
                s["short_usd"] += e.value_usd
        return dict(stats)

    def long_short_ratio(self) -> tuple[float, float]:
        long_total = sum(e.value_usd for e in self.events if e.side == "long")
        short_total = sum(e.value_usd for e in self.events if e.side == "short")
        total = long_total + short_total
        if total == 0:
            return 50.0, 50.0
        return round(long_total / total * 100, 1), round(short_total / total * 100, 1)

    def by_exchange(self) -> dict[str, ExchangeStats]:
        stats: dict[str, ExchangeStats] = defaultdict(
            lambda: {"count": 0, "total_usd": 0.0}
        )
        for e in self.events:
            s = stats[e.exchange]
            s["count"] += 1
            s["total_usd"] += e.value_usd
        return dict(stats)

    def recent_feed(self, limit: int = 15) -> list[LiquidationEvent]:
        return list(self.events)[-limit:][::-1]

    def total_24h(self) -> float:
        return sum(e.value_usd for e in self.events)
