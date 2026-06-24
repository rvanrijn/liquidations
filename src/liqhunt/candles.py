# src/liqhunt/candles.py
"""Binance 5-minute kline fetcher with rolling stats."""

import logging

import aiohttp

from src.liqhunt.models import Candle

logger = logging.getLogger(__name__)

KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
INTERVAL = "5m"
LOOKBACK = 50
AVG_WINDOW = 20


class CandleFetcher:
    """Fetches 5m BTC candles from Binance Futures REST API."""

    def __init__(self):
        self._candles: list[Candle] = []
        self._last_closed_ts: int = 0  # cache key

    async def fetch(self, session: aiohttp.ClientSession) -> list[Candle]:
        """Fetch latest 5m candles. Returns closed candles only (newest last).

        Caches within the same 5m window to avoid redundant API calls.
        """
        try:
            params = {"symbol": "BTCUSDT", "interval": INTERVAL, "limit": LOOKBACK}
            async with session.get(KLINES_URL, params=params) as resp:
                if resp.status != 200:
                    logger.warning("Kline fetch failed: HTTP %d", resp.status)
                    return self._candles
                raw = await resp.json()
        except Exception as e:
            logger.warning("Kline fetch error: %s", e)
            return self._candles

        if not raw:
            return self._candles

        # Parse all candles, drop the last one (in-progress)
        all_candles = [self._parse(k) for k in raw]
        closed = all_candles[:-1] if len(all_candles) > 1 else all_candles

        # Check if we have new data
        if closed and closed[-1].timestamp == self._last_closed_ts:
            return self._candles  # same 5m window, return cache

        self._candles = closed
        if closed:
            self._last_closed_ts = closed[-1].timestamp
        return self._candles

    @property
    def avg_range(self) -> float:
        """Rolling mean of candle ranges over last AVG_WINDOW closed candles."""
        recent = self._candles[-AVG_WINDOW:]
        if not recent:
            return 0.0
        return sum(c.range for c in recent) / len(recent)

    @property
    def latest_closed(self) -> Candle | None:
        """Most recent completed candle."""
        return self._candles[-1] if self._candles else None

    @property
    def candles(self) -> list[Candle]:
        return self._candles

    @staticmethod
    def _parse(kline: list) -> Candle:
        """Parse a Binance kline array into a Candle."""
        return Candle(
            open=float(kline[1]),
            high=float(kline[2]),
            low=float(kline[3]),
            close=float(kline[4]),
            volume=float(kline[5]),
            timestamp=int(kline[0]),
        )
