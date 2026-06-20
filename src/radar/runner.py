"""RadarRunner — read-only orchestration of the engine's live perception.

Imports NO trader or trading-module and performs NO DB writes (see test_readonly).
"""

import asyncio
import logging
from collections import deque
from time import time as _time
from typing import Callable, Optional

from src.radar import compute
from src.radar.builder import build_state
from src.radar.state import RadarState, liq_message

logger = logging.getLogger(__name__)

POLL_SEC = 10  # match the bot's ~10s cycle cadence


class RadarRunner:
    def __init__(self, engine=None):
        # Lazy import so unit tests can inject a fake engine without network deps.
        if engine is None:
            from src.liqhunt.signal_engine import SignalEngine
            engine = SignalEngine()
        self.engine = engine
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

        quality = signal.quality_score if signal is not None else 0.0
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
        return self.state

    # ---- live wiring (exercised manually / smoke-tested, not in unit tests) ----

    async def run(self) -> None:
        """Wire real feeds and loop forever. Read-only."""
        from types import SimpleNamespace

        from src.liqhunt.candles import CandleFetcher
        from src.liqlevels.client import BinanceLiqClient
        from src.liqlevels.database import MagnetDatabase
        from src.liqlevels.monitor import MagnetMonitor
        from src.orderflow.aggregator import OrderFlowAggregator
        from src.orderflow.clients.binance import BinanceTradeClient
        from src.radar.feeds import RadarLiqFeed

        client = BinanceLiqClient()
        # MagnetDatabase is opened read-only for snapshot/levels derivation only.
        magnet_db = MagnetDatabase()
        monitor = MagnetMonitor(magnet_db)
        candle_fetcher = CandleFetcher()
        btc_flow = OrderFlowAggregator(window_minutes=1)

        def on_btc_trade(event):
            if event.coin == "BTC":
                btc_flow.add_event(event)

        btc_ws = BinanceTradeClient(coins=["BTC"], on_event=on_btc_trade)

        def _emit_liq(ts, side, usd, price):
            if self.on_liq:
                self.on_liq(liq_message(ts, side, usd, price))

        liq_feed = RadarLiqFeed(symbol="btcusdt", on_event=_emit_liq)

        await self._seed_history(client)

        asyncio.create_task(btc_ws.connect())
        asyncio.create_task(liq_feed.connect())

        while True:
            try:
                data = await client.get_all_coins(["BTC", "ETH", "SOL"])
                btc = data.get("BTC")
                price = btc.current_price if btc else 0.0
                if btc:
                    monitor.update(btc, obv_macd_hist=candle_fetcher.obv_macd_histogram)
                await client._ensure_session()
                candles = await candle_fetcher.fetch(client.session)
                _, _, cvd_1m = btc_flow.totals()
                taker_ratio = await client.get_taker_ratio("BTC") or 1.0
                liq_long_usd, liq_short_usd, _ = liq_feed.totals()
                market = SimpleNamespace(
                    price=price,
                    snapshot=monitor.snapshot,
                    candles=candles,
                    avg_range=candle_fetcher.avg_range,
                    liq_levels_long=[(l.price, l.estimated_usd) for l in btc.longs_at_risk] if btc else [],
                    liq_levels_short=[(l.price, l.estimated_usd) for l in btc.shorts_at_risk] if btc else [],
                    funding=btc.funding_rate if btc else 0.0,
                    taker_ratio=taker_ratio,
                    liq_long_usd=liq_long_usd,
                    liq_short_usd=liq_short_usd,
                    oi_usd=btc.open_interest_usd if btc else 0.0,
                    cvd_1m=cvd_1m,
                    connections={"liq_feed": liq_feed.connected,
                                 "trade_feed": getattr(btc_ws, "connected", False)},
                )
                self.build_once(market, now=_time())
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
