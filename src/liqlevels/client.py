# src/liqlevels/client.py
"""Coinglass API client for liquidation data."""

import os
import asyncio
import aiohttp
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

BASE_URL = "https://open-api-v4.coinglass.com/api"


@dataclass
class LiquidationLevel:
    """A liquidation level with price and amounts."""
    price: float
    long_liq_usd: float
    short_liq_usd: float
    percent_from_current: float  # Positive = above, negative = below


@dataclass
class CoinLiquidations:
    """Liquidation data for a single coin."""
    coin: str
    current_price: float
    longs_at_risk: list[LiquidationLevel]  # Sorted by % (closest first)
    shorts_at_risk: list[LiquidationLevel]  # Sorted by % (closest first)
    total_long_liq_usd: float
    total_short_liq_usd: float


class CoinglassClient:
    """Client for Coinglass API."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("COINGLASS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "COINGLASS_API_KEY not set. Get one at https://www.coinglass.com/pricing"
            )
        self.session: aiohttp.ClientSession | None = None

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={"CG-API-KEY": self.api_key}
            )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def get_liquidation_map(self, symbol: str = "BTC", range: str = "1d") -> CoinLiquidations | None:
        """Get liquidation map data for a symbol.

        Args:
            symbol: Coin symbol (e.g., "BTC", "ETH")
            range: Time range - "1d", "7d", or "30d"

        Returns:
            CoinLiquidations with price levels and amounts
        """
        await self._ensure_session()

        url = f"{BASE_URL}/futures/liquidation/aggregated-heatmap/model2"
        params = {"symbol": symbol}

        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"Coinglass API error: {resp.status}")
                    return None

                data = await resp.json()
                if data.get("code") != "0":
                    logger.warning(f"Coinglass API error: {data.get('msg')}")
                    return None

                return self._parse_liquidation_data(symbol, data.get("data", []))

        except Exception as e:
            logger.error(f"Error fetching liquidation data: {e}")
            return None

    def _parse_liquidation_data(self, symbol: str, data: list) -> CoinLiquidations | None:
        """Parse raw API response into CoinLiquidations."""
        if not data:
            return None

        # Data comes as list of [price, liq_amount, ...] or similar
        # Adjust parsing based on actual API response format
        longs = []
        shorts = []
        current_price = 0.0
        total_long = 0.0
        total_short = 0.0

        # Try to find current price from the data
        # The API might return price levels with liquidation amounts
        for item in data:
            if isinstance(item, dict):
                price = float(item.get("price", item.get("y", 0)))
                liq_value = float(item.get("liquidationUsd", item.get("liqUsdValue", 0)))
                # Determine if this is long or short liq based on position
                # Usually lower prices = long liquidations, higher = short
                if price > 0 and liq_value > 0:
                    level = LiquidationLevel(
                        price=price,
                        long_liq_usd=liq_value,
                        short_liq_usd=0,
                        percent_from_current=0,
                    )
                    longs.append(level)
                    total_long += liq_value
            elif isinstance(item, list) and len(item) >= 2:
                price = float(item[0])
                liq_value = float(item[1])
                if price > 0:
                    level = LiquidationLevel(
                        price=price,
                        long_liq_usd=liq_value,
                        short_liq_usd=0,
                        percent_from_current=0,
                    )
                    longs.append(level)
                    total_long += liq_value

        return CoinLiquidations(
            coin=symbol,
            current_price=current_price,
            longs_at_risk=sorted(longs, key=lambda x: abs(x.percent_from_current))[:10],
            shorts_at_risk=sorted(shorts, key=lambda x: abs(x.percent_from_current))[:10],
            total_long_liq_usd=total_long,
            total_short_liq_usd=total_short,
        )

    async def get_all_coins(self, coins: list[str]) -> dict[str, CoinLiquidations]:
        """Fetch liquidation data for multiple coins."""
        await self._ensure_session()

        results = {}
        for coin in coins:
            data = await self.get_liquidation_map(coin)
            if data:
                results[coin] = data
            await asyncio.sleep(0.2)  # Rate limiting

        return results
