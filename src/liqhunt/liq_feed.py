# src/liqhunt/liq_feed.py
"""Real-time Binance liquidation feed via forceOrder WebSocket."""

import asyncio
import json
import logging
from collections import deque
from time import time

logger = logging.getLogger(__name__)


class LiqFeed:
    """Streams real-time liquidation events from Binance futures."""

    def __init__(self, symbol: str = "btcusdt"):
        self._events: deque[tuple[float, str, float]] = deque(maxlen=300)  # ~5 min
        self._ws_url = f"wss://fstream.binance.com/ws/{symbol}@forceOrder"
        self.connected = False
        self._reconnect_delay = 1

    async def connect(self) -> None:
        """Connect to WebSocket with auto-reconnect."""
        import websockets

        while True:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    self.connected = True
                    self._reconnect_delay = 1
                    logger.info("LiqFeed: Connected to %s", self._ws_url)

                    async for raw_msg in ws:
                        try:
                            data = json.loads(raw_msg)
                            self._on_message(data)
                        except Exception as e:
                            logger.debug("LiqFeed: Parse error: %s", e)

            except Exception as e:
                self.connected = False
                logger.warning(
                    "LiqFeed: Disconnected (%s), reconnecting in %ds",
                    e, self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 30)

    def _on_message(self, data: dict) -> None:
        """Parse a forceOrder event and store it."""
        o = data.get("o", {})
        side = o.get("S")  # "SELL" = long liq, "BUY" = short liq
        qty = float(o.get("z", 0))
        avg_price = float(o.get("ap", 0))
        if qty <= 0 or avg_price <= 0:
            return
        usd = qty * avg_price
        self._events.append((time(), side, usd))

    def _cleanup(self) -> None:
        """Remove events older than 5 minutes."""
        cutoff = time() - 5 * 60
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def totals(self) -> tuple[float, float, float]:
        """Return (long_liq_usd, short_liq_usd, liq_rate_per_min) over 5min window."""
        self._cleanup()
        long_liq = sum(usd for _, side, usd in self._events if side == "SELL")
        short_liq = sum(usd for _, side, usd in self._events if side == "BUY")
        total = long_liq + short_liq
        return long_liq, short_liq, total / 5
