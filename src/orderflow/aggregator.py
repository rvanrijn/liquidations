"""Order flow aggregator for tracking and analyzing trade events."""

import time
from collections import deque
from typing import Deque

from src.orderflow.models import TradeEvent


class OrderFlowAggregator:
    """Aggregates trade events within a time window for analysis."""

    # Size thresholds in USD
    WHALE_THRESHOLD = 1_000_000  # >= $1,000,000
    LARGE_THRESHOLD = 100_000  # >= $100,000
    MEDIUM_THRESHOLD = 10_000  # >= $10,000
    SMALL_THRESHOLD = 1_000  # >= $1,000 (filter out below)

    def __init__(self, window_minutes: int = 15) -> None:
        """Initialize aggregator with time window.

        Args:
            window_minutes: Time window in minutes to keep events.
        """
        self._events: Deque[TradeEvent] = deque()
        self._window_ms = window_minutes * 60 * 1000

    def _classify_size(self, value_usd: float) -> str:
        """Classify trade size based on USD value.

        Args:
            value_usd: Trade value in USD.

        Returns:
            Size category: "whale", "large", "medium", or "small".
        """
        if value_usd >= self.WHALE_THRESHOLD:
            return "whale"
        elif value_usd >= self.LARGE_THRESHOLD:
            return "large"
        elif value_usd >= self.MEDIUM_THRESHOLD:
            return "medium"
        else:
            return "small"

    def add_event(self, event: TradeEvent) -> None:
        """Add a trade event to the aggregator.

        Args:
            event: TradeEvent to add.
        """
        self._events.append(event)
        self._prune()

    def _prune(self) -> None:
        """Remove events older than the time window."""
        current_time_ms = int(time.time() * 1000)
        cutoff = current_time_ms - self._window_ms

        while self._events and self._events[0].timestamp < cutoff:
            self._events.popleft()

    def by_exchange(self) -> dict:
        """Aggregate events by exchange.

        Returns:
            Dict mapping exchange to buy/sell stats:
            {exchange: {"buy_usd": float, "sell_usd": float, "delta": float, "count": int}}
        """
        result: dict = {}

        for event in self._events:
            if event.value_usd < self.SMALL_THRESHOLD:
                continue

            if event.exchange not in result:
                result[event.exchange] = {
                    "buy_usd": 0.0,
                    "sell_usd": 0.0,
                    "delta": 0.0,
                    "count": 0,
                }

            stats = result[event.exchange]
            stats["count"] += 1

            if event.side == "buy":
                stats["buy_usd"] += event.value_usd
            else:
                stats["sell_usd"] += event.value_usd

            stats["delta"] = stats["buy_usd"] - stats["sell_usd"]

        return result

    def by_size(self) -> dict:
        """Aggregate events by size category.

        Returns:
            Dict mapping size category to buy/sell stats:
            {category: {"buy_usd": float, "sell_usd": float, "delta": float, "count": int}}
        """
        result: dict = {
            "whale": {"buy_usd": 0.0, "sell_usd": 0.0, "delta": 0.0, "count": 0},
            "large": {"buy_usd": 0.0, "sell_usd": 0.0, "delta": 0.0, "count": 0},
            "medium": {"buy_usd": 0.0, "sell_usd": 0.0, "delta": 0.0, "count": 0},
            "small": {"buy_usd": 0.0, "sell_usd": 0.0, "delta": 0.0, "count": 0},
        }

        for event in self._events:
            if event.value_usd < self.SMALL_THRESHOLD:
                continue

            category = self._classify_size(event.value_usd)
            stats = result[category]
            stats["count"] += 1

            if event.side == "buy":
                stats["buy_usd"] += event.value_usd
            else:
                stats["sell_usd"] += event.value_usd

            stats["delta"] = stats["buy_usd"] - stats["sell_usd"]

        return result

    def totals(self) -> tuple:
        """Calculate total buy/sell volumes.

        Returns:
            Tuple of (total_buy_usd, total_sell_usd, delta).
        """
        total_buy = 0.0
        total_sell = 0.0

        for event in self._events:
            if event.value_usd < self.SMALL_THRESHOLD:
                continue

            if event.side == "buy":
                total_buy += event.value_usd
            else:
                total_sell += event.value_usd

        return (total_buy, total_sell, total_buy - total_sell)

    def buy_sell_pressure(self) -> tuple:
        """Calculate buy/sell pressure as percentages.

        Returns:
            Tuple of (buy_pct, sell_pct) as percentages (0-100).
        """
        total_buy, total_sell, _ = self.totals()
        total_volume = total_buy + total_sell

        if total_volume == 0:
            return (0.0, 0.0)

        buy_pct = (total_buy / total_volume) * 100
        sell_pct = (total_sell / total_volume) * 100

        return (buy_pct, sell_pct)

    def recent_feed(self, n: int = 15) -> list:
        """Get recent large events.

        Args:
            n: Number of events to return.

        Returns:
            List of last n events with value_usd >= $100,000, newest first.
        """
        large_events = [
            event
            for event in self._events
            if event.value_usd >= self.LARGE_THRESHOLD
        ]

        # Return newest first (reverse order), limited to n
        return list(reversed(large_events))[:n]
