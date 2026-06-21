"""RadarRunner — read-only orchestration of the engine's live perception.

Imports NO trader or trading-module and performs NO DB writes (see test_readonly).
"""

import asyncio
import logging
import os
from collections import deque
from time import time as _time
from typing import Callable, Optional

from src.liqlevels.models import LiqSnapshot
from src.radar import compute
from src.radar.builder import build_state
from src.radar.state import RadarState, liq_message

logger = logging.getLogger(__name__)

POLL_SEC = 10  # match the bot's ~10s cycle cadence


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def apply_dev_overrides(ignore_day_filter: bool) -> None:
    """Dev/test only: relax engine gates for visualization.

    Mutates the SignalEngine module's in-memory SKIP_DAYS global for THIS process
    only — it does not edit engine source and does not affect the real bot (a
    separate process). Lets the radar render the full evaluation pipeline (and
    ARMED states) on days the bot would normally skip, e.g. Saturday.
    """
    if not ignore_day_filter:
        return
    import src.liqhunt.signal_engine as se
    se.SKIP_DAYS = set()
    logger.warning("RADAR dev override: day filter DISABLED (visualization/test only)")


def build_snapshot(btc, now: float):
    """Build a fresh read-only LiqSnapshot from current liq data. No DB, no monitor."""
    if btc is None:
        return None
    total_long = sum(l.estimated_usd for l in btc.longs_at_risk)
    total_short = sum(l.estimated_usd for l in btc.shorts_at_risk)
    if total_long <= 0 and total_short <= 0:
        return None
    return LiqSnapshot(
        btc_price=btc.current_price,
        total_long_usd=total_long,
        total_short_usd=total_short,
        timestamp=now,
    )


_FAPI = "https://fapi.binance.com/fapi/v1"


async def _fetch_funding(client) -> float:
    """Current BTCUSDT perpetual funding rate from the public premiumIndex."""
    try:
        await client._ensure_session()
        async with client.session.get(
            f"{_FAPI}/premiumIndex", params={"symbol": "BTCUSDT"}
        ) as resp:
            if resp.status == 200:
                d = await resp.json()
                return float(d.get("lastFundingRate", 0.0))
    except Exception:
        pass
    return 0.0


async def _fetch_cvd(client):
    """CVD from 1m-kline taker volumes (robust REST, no trade WS).

    Returns (cvd_1m_usd, cvd_30m_usd, ok). Per-candle delta in quote (USD) =
    2*takerBuyQuote - quoteVolume; latest candle = 1m flow, sum of last 30 = 30m.
    """
    try:
        await client._ensure_session()
        async with client.session.get(
            f"{_FAPI}/klines",
            params={"symbol": "BTCUSDT", "interval": "1m", "limit": 31},
        ) as resp:
            if resp.status != 200:
                return 0.0, 0.0, False
            klines = await resp.json()
        deltas = [2.0 * float(k[10]) - float(k[7]) for k in klines]  # k[10]=takerBuyQuote, k[7]=quoteVol
        if not deltas:
            return 0.0, 0.0, True
        return deltas[-1], sum(deltas[-30:]), True
    except Exception:
        return 0.0, 0.0, False


def _live_quality(snapshot, oi_delta, oi_vel, range_6h, funding, taker_ratio) -> float:
    """The engine's pure quality score for the current state, gates aside.

    Lets the radar show a live 0..1 quality even while STANDBY (the engine only
    attaches a score to an armed Signal). Same function the engine uses.
    """
    from src.liqhunt.quality import compute_quality_score
    return compute_quality_score(
        oi_change_pct=oi_delta,
        imbalance_ratio=snapshot.imbalance_ratio,
        funding_rate=funding,
        price_range_6h=range_6h,
        oi_velocity=oi_vel,
        taker_ratio=taker_ratio,
        magnet_side=snapshot.bigger_side,
    )


class RadarRunner:
    def __init__(self, engine=None, ignore_day_filter: Optional[bool] = None):
        # Lazy import so unit tests can inject a fake engine without network deps.
        if engine is None:
            from src.liqhunt.signal_engine import SignalEngine
            engine = SignalEngine()
        self.engine = engine
        if ignore_day_filter is None:
            ignore_day_filter = _env_truthy("RADAR_IGNORE_DAY_FILTER")
        self.ignore_day_filter = ignore_day_filter
        self.oi_history: deque = deque(maxlen=60)
        self.cvd_history: deque = deque(maxlen=30)
        self.price_history: deque = deque(maxlen=2200)  # (ts, price)
        self.state: Optional[RadarState] = None
        self.on_liq: Optional[Callable[[dict], None]] = None

    def build_once(self, market, now: float) -> RadarState:
        """Compute one RadarState from a market read. Pure of network/DB."""
        if market.price > 0:
            self.price_history.append((now, market.price))
        if market.oi_usd > 0:
            self.oi_history.append(market.oi_usd)
        self.cvd_history.append(market.cvd_1m)

        oi_delta = compute.oi_change_pct(self.oi_history)
        oi_vel = compute.oi_velocity(self.oi_history)
        # Prefer a precomputed cvd_30m from the market read (run() supplies a
        # robust kline-derived value); fall back to the rolling deque sum.
        cvd_30m = getattr(market, "cvd_30m", None)
        if cvd_30m is None:
            cvd_30m = compute.cvd_sum(self.cvd_history)
        range_6h = compute.price_range_6h(self.price_history, now=now)

        signal = self.engine.evaluate(
            snapshot=market.snapshot,
            candles=market.candles,
            avg_range=market.avg_range,
            btc_price=market.price,
            liq_levels_long=market.liq_levels_long,
            liq_levels_short=market.liq_levels_short,
            btc_delta_1m=market.cvd_1m,
            oi_change_pct=oi_delta,
            funding_rate=market.funding,
            price_range_6h=range_6h,
            oi_velocity=oi_vel,
            liq_long_usd=market.liq_long_usd,
            liq_short_usd=market.liq_short_usd,
            taker_ratio=market.taker_ratio,
            cvd_30m=cvd_30m,
        )

        # Live quality every cycle (not only when armed): the same pure score the
        # engine assigns, so the gauge is meaningful in STANDBY too.
        if signal is not None:
            quality = signal.quality_score
        elif market.snapshot is not None:
            quality = _live_quality(market.snapshot, oi_delta, oi_vel, range_6h,
                                    market.funding, market.taker_ratio)
        else:
            quality = 0.0
        self.state = build_state(
            snapshot=market.snapshot,
            signal=signal,
            rejection_reason=self.engine.rejection_reason,
            price=market.price,
            oi_delta_pct=oi_delta,
            oi_velocity=oi_vel,
            cvd_30m=cvd_30m,
            funding=market.funding,
            taker_ratio=market.taker_ratio,
            quality=quality,
            range_6h=range_6h,
            liq_levels_long=market.liq_levels_long,
            liq_levels_short=market.liq_levels_short,
            connections=market.connections,
        )
        self.state.dev_mode = self.ignore_day_filter
        return self.state

    # ---- live wiring (exercised manually / smoke-tested, not in unit tests) ----

    async def run(self) -> None:
        """Wire real feeds and loop forever. Read-only."""
        apply_dev_overrides(self.ignore_day_filter)
        from types import SimpleNamespace

        from src.liqhunt.candles import CandleFetcher
        from src.liqlevels.client import BinanceLiqClient
        from src.radar.feeds import RadarLiqFeed

        client = BinanceLiqClient()
        candle_fetcher = CandleFetcher()

        def _emit_liq(ts, side, usd, price):
            if self.on_liq:
                self.on_liq(liq_message(ts, side, usd, price))

        liq_feed = RadarLiqFeed(symbol="btcusdt", on_event=_emit_liq)

        await self._seed_history(client)
        asyncio.create_task(liq_feed.connect())

        while True:
            try:
                data = await client.get_all_coins(["BTC", "ETH", "SOL"])
                btc = data.get("BTC")
                price = btc.current_price if btc else 0.0
                now = _time()
                await client._ensure_session()
                candles = await candle_fetcher.fetch(client.session)
                # Funding + CVD from robust public REST (no flaky trade WS):
                # premiumIndex for funding, 1m-kline taker volumes for CVD.
                funding = await _fetch_funding(client)
                cvd_1m, cvd_30m, rest_ok = await _fetch_cvd(client)
                taker_ratio = 1.0
                _get_taker = getattr(client, "get_taker_ratio", None)
                if _get_taker is not None:
                    taker_ratio = await _get_taker("BTC") or 1.0
                liq_long_usd, liq_short_usd, _ = liq_feed.totals()
                market = SimpleNamespace(
                    price=price,
                    snapshot=build_snapshot(btc, now),
                    candles=candles,
                    avg_range=candle_fetcher.avg_range,
                    liq_levels_long=[(l.price, l.estimated_usd) for l in btc.longs_at_risk] if btc else [],
                    liq_levels_short=[(l.price, l.estimated_usd) for l in btc.shorts_at_risk] if btc else [],
                    funding=funding,
                    taker_ratio=taker_ratio,
                    liq_long_usd=liq_long_usd,
                    liq_short_usd=liq_short_usd,
                    oi_usd=btc.open_interest_usd if btc else 0.0,
                    cvd_1m=cvd_1m,
                    cvd_30m=cvd_30m,
                    connections={"liq_feed": liq_feed.connected, "trade_feed": rest_ok},
                )
                self.build_once(market, now=now)
            except Exception:
                logger.exception("radar cycle failed; continuing")
            await asyncio.sleep(POLL_SEC)

    async def _seed_history(self, client) -> None:
        """Warm OI + price history like the bot does. Best-effort."""
        try:
            await client._ensure_session()
            async with client.session.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": "BTCUSDT", "interval": "5m", "limit": 72},
            ) as resp:
                if resp.status == 200:
                    for k in await resp.json():
                        ts = float(k[0]) / 1000.0
                        self.price_history.append((ts, float(k[2])))
                        self.price_history.append((ts, float(k[3])))
            async with client.session.get(
                "https://fapi.binance.com/futures/data/openInterestHist",
                params={"symbol": "BTCUSDT", "period": "5m", "limit": 3},
            ) as resp:
                if resp.status == 200:
                    for point in await resp.json():
                        oi_val = float(point.get("sumOpenInterestValue", 0))
                        if oi_val > 0:
                            self.oi_history.append(oi_val)
        except Exception:
            logger.warning("radar history seed failed; will warm up live")
