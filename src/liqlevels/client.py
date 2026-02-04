# src/liqlevels/client.py
"""Binance API client for estimating liquidation levels."""

import asyncio
import aiohttp
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

BINANCE_BASE = "https://fapi.binance.com"

# Common leverage levels used by traders
LEVERAGE_LEVELS = [100, 50, 25, 10, 5]


@dataclass
class LiquidationLevel:
    """A liquidation level with price and estimated amounts."""
    leverage: int
    price: float
    percent_from_current: float  # Negative = below (longs liq), Positive = above (shorts liq)
    estimated_usd: float  # Estimated $ at risk


@dataclass
class CoinLiquidations:
    """Liquidation data for a single coin."""
    coin: str
    current_price: float
    open_interest_usd: float
    longs_at_risk: list[LiquidationLevel]  # Price drops trigger these
    shorts_at_risk: list[LiquidationLevel]  # Price rises trigger these


class BinanceLiqClient:
    """Client for Binance Futures API to estimate liquidation levels."""

    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def get_price(self, symbol: str) -> float | None:
        """Get current price for a symbol."""
        await self._ensure_session()

        url = f"{BINANCE_BASE}/fapi/v1/ticker/price"
        params = {"symbol": f"{symbol}USDT"}

        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return float(data.get("price", 0))
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return None

    async def get_open_interest(self, symbol: str) -> float | None:
        """Get open interest in USD for a symbol."""
        await self._ensure_session()

        url = f"{BINANCE_BASE}/fapi/v1/openInterest"
        params = {"symbol": f"{symbol}USDT"}

        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                oi = float(data.get("openInterest", 0))

            # Get price to convert to USD
            price = await self.get_price(symbol)
            if price:
                return oi * price
            return None

        except Exception as e:
            logger.error(f"Error fetching OI for {symbol}: {e}")
            return None

    async def get_long_short_ratio(self, symbol: str) -> tuple[float, float] | None:
        """Get long/short account ratio."""
        await self._ensure_session()

        url = f"{BINANCE_BASE}/futures/data/globalLongShortAccountRatio"
        params = {"symbol": f"{symbol}USDT", "period": "5m", "limit": 1}

        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if data:
                    ratio = float(data[0].get("longShortRatio", 1))
                    # Convert ratio to percentages
                    long_pct = ratio / (1 + ratio) * 100
                    short_pct = 100 - long_pct
                    return (long_pct, short_pct)
            return None
        except Exception as e:
            logger.error(f"Error fetching L/S ratio for {symbol}: {e}")
            return None

    def _calculate_liquidation_levels(
        self,
        current_price: float,
        open_interest_usd: float,
        long_pct: float,
        short_pct: float,
    ) -> tuple[list[LiquidationLevel], list[LiquidationLevel]]:
        """Calculate estimated liquidation levels at common leverage points.

        For longs: liquidation when price drops ~(1/leverage) below entry
        For shorts: liquidation when price rises ~(1/leverage) above entry
        """
        longs = []
        shorts = []

        # Estimate OI split between longs and shorts
        long_oi = open_interest_usd * (long_pct / 100)
        short_oi = open_interest_usd * (short_pct / 100)

        # Assume OI is distributed across leverage levels
        # Higher leverage = smaller portion but closer to liquidation
        leverage_weights = {100: 0.05, 50: 0.10, 25: 0.20, 10: 0.35, 5: 0.30}

        for leverage in LEVERAGE_LEVELS:
            weight = leverage_weights.get(leverage, 0.1)

            # Long liquidation: price drops by ~(1/leverage)
            # Using 0.9/leverage to account for maintenance margin
            long_liq_pct = -0.9 / leverage * 100
            long_liq_price = current_price * (1 + long_liq_pct / 100)
            long_liq_usd = long_oi * weight

            longs.append(LiquidationLevel(
                leverage=leverage,
                price=long_liq_price,
                percent_from_current=long_liq_pct,
                estimated_usd=long_liq_usd,
            ))

            # Short liquidation: price rises by ~(1/leverage)
            short_liq_pct = 0.9 / leverage * 100
            short_liq_price = current_price * (1 + short_liq_pct / 100)
            short_liq_usd = short_oi * weight

            shorts.append(LiquidationLevel(
                leverage=leverage,
                price=short_liq_price,
                percent_from_current=short_liq_pct,
                estimated_usd=short_liq_usd,
            ))

        # Sort by proximity to current price
        longs.sort(key=lambda x: x.percent_from_current, reverse=True)
        shorts.sort(key=lambda x: x.percent_from_current)

        return longs, shorts

    async def get_coin_liquidations(self, symbol: str) -> CoinLiquidations | None:
        """Get estimated liquidation levels for a coin."""
        price = await self.get_price(symbol)
        if not price:
            return None

        oi = await self.get_open_interest(symbol)
        if not oi:
            return None

        ls_ratio = await self.get_long_short_ratio(symbol)
        long_pct, short_pct = ls_ratio if ls_ratio else (50.0, 50.0)

        longs, shorts = self._calculate_liquidation_levels(price, oi, long_pct, short_pct)

        return CoinLiquidations(
            coin=symbol,
            current_price=price,
            open_interest_usd=oi,
            longs_at_risk=longs,
            shorts_at_risk=shorts,
        )

    async def get_all_coins(self, coins: list[str]) -> dict[str, CoinLiquidations]:
        """Fetch liquidation data for multiple coins."""
        await self._ensure_session()

        results = {}
        for coin in coins:
            data = await self.get_coin_liquidations(coin)
            if data:
                results[coin] = data
            await asyncio.sleep(0.1)  # Rate limiting

        return results
