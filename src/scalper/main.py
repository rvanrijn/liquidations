# src/scalper/main.py
import asyncio
import logging
from collections import deque
from time import time

from src.orderflow.aggregator import OrderFlowAggregator
from src.orderflow.clients.bybit import BybitTradeClient
from src.orderflow.clients.binance import BinanceTradeClient
from src.liqlevels.client import BinanceLiqClient
from src.scalper.models import BotState
from src.scalper.vwap import VWAPCalculator
from src.scalper.hyperliquid import HyperliquidPriceFeed
from src.scalper.strategy import calculate_bias, check_short_entry, check_long_entry, create_position, check_exit
from src.scalper.paper_engine import PaperEngine
from src.scalper.database import TradeDatabase
from src.scalper.ui.dashboard import ScalperDashboard

COINS = ["BTC"]  # Only BTC for scalping

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


async def run_scalper():
    # 1. Create aggregators for 5m and 15m
    agg_5m = OrderFlowAggregator(window_minutes=5)
    agg_15m = OrderFlowAggregator(window_minutes=15)

    # 2. VWAP calculator
    vwap_calc = VWAPCalculator()

    # 3. Track large trades in last 30 seconds for entry filter
    large_buys_30s: deque = deque()   # timestamps of large buys (>$100K)
    large_sells_30s: deque = deque()  # timestamps of large sells (>$100K)

    # 4. Rolling 5m high/low
    price_history_5m: deque = deque()  # (timestamp_ms, price) tuples

    # 5. Event handler for orderflow trades
    def on_trade(event):
        """Feed trades to aggregators, VWAP, and large trade tracker."""
        agg_5m.add_event(event)
        agg_15m.add_event(event)

        # Only BTC for VWAP and price tracking
        if event.coin == "BTC":
            vwap_calc.update(event.price, event.size)

            now = int(time() * 1000)
            price_history_5m.append((now, event.price))
            # Prune older than 5 minutes
            cutoff = now - 5 * 60 * 1000
            while price_history_5m and price_history_5m[0][0] < cutoff:
                price_history_5m.popleft()

            # Track large trades (>$100K)
            if event.value_usd >= 100_000:
                if event.side == "buy":
                    large_buys_30s.append(now)
                else:
                    large_sells_30s.append(now)

    # 6. Create WebSocket clients
    bybit = BybitTradeClient(coins=COINS, on_event=on_trade)
    binance = BinanceTradeClient(coins=COINS, on_event=on_trade)

    # 7. Hyperliquid price feed
    hl_feed = HyperliquidPriceFeed(coin="BTC")

    # 8. Liq levels client
    liq_client = BinanceLiqClient()

    # 9. Paper engine + database
    db = TradeDatabase()
    engine = PaperEngine(db)

    # 10. Dashboard
    dashboard = ScalperDashboard(engine.state)

    def get_high_5m() -> float:
        if not price_history_5m:
            return 0.0
        return max(p for _, p in price_history_5m)

    def get_low_5m() -> float:
        if not price_history_5m:
            return 0.0
        return min(p for _, p in price_history_5m)

    def has_large_buys_recent() -> bool:
        now = int(time() * 1000)
        cutoff = now - 30_000  # 30 seconds
        while large_buys_30s and large_buys_30s[0] < cutoff:
            large_buys_30s.popleft()
        return len(large_buys_30s) > 0

    def has_large_sells_recent() -> bool:
        now = int(time() * 1000)
        cutoff = now - 30_000
        while large_sells_30s and large_sells_30s[0] < cutoff:
            large_sells_30s.popleft()
        return len(large_sells_30s) > 0

    # Strategy loop - runs every 1 second
    async def strategy_loop():
        liq_data = None
        liq_last_fetch = 0

        while True:
            try:
                price = hl_feed.current_price
                if price <= 0:
                    await asyncio.sleep(1)
                    continue

                # Refresh liq data every 30 seconds
                now = time()
                if now - liq_last_fetch > 30:
                    liq_data = await liq_client.get_coin_liquidations("BTC")
                    liq_last_fetch = now

                # Check daily reset
                engine.check_daily_reset()

                # Get aggregator data
                buy_15m, sell_15m, delta_15m = agg_15m.totals()
                buy_5m, sell_5m, delta_5m = agg_5m.totals()
                buy_pct_5m, _ = agg_5m.buy_sell_pressure()

                vwap = vwap_calc.vwap
                high_5m = get_high_5m()
                low_5m = get_low_5m()

                # Calculate liq zone values by summing estimated_usd
                long_liq = sum(l.estimated_usd for l in liq_data.longs_at_risk) if liq_data else 0
                short_liq = sum(l.estimated_usd for l in liq_data.shorts_at_risk) if liq_data else 0

                # Nearest liq prices (for TP2)
                nearest_long_liq_price = liq_data.longs_at_risk[0].price if liq_data and liq_data.longs_at_risk else price * 0.98
                nearest_short_liq_price = liq_data.shorts_at_risk[0].price if liq_data and liq_data.shorts_at_risk else price * 1.02

                # NOTE: For liqlevels, long_liq is the estimated $ of LONGS at risk (below price)
                # and short_liq is SHORTS at risk (above price)

                # Calculate bias
                bias = calculate_bias(long_liq, short_liq, delta_15m, price, vwap)
                engine.state.last_bias = bias

                # Build market snapshot for trade logging
                market_snapshot = {
                    "delta_5m": delta_5m,
                    "delta_15m": delta_15m,
                    "buy_pressure": buy_pct_5m,
                    "vwap": vwap,
                    "long_liq_usd": long_liq,
                    "short_liq_usd": short_liq,
                    "bias": bias.direction,
                }

                # Check exits first
                if engine.state.position:
                    exit_reason = check_exit(engine.state.position, price, delta_5m)
                    if exit_reason:
                        record = engine.close_position(price, exit_reason, market_snapshot)
                        if record:
                            logger.info(f"CLOSED: {record.exit_reason} PnL: ${record.pnl_usd:+.2f} ({record.r_multiple:+.1f}R)")

                # Check entries (only if no position)
                if engine.state.position is None and engine.state.is_active:
                    if check_short_entry(bias, price, high_5m, delta_5m, buy_pct_5m, has_large_buys_recent(), engine.state):
                        pos = create_position("SHORT", price, engine.state, nearest_long_liq_price, "Short entry signal")
                        engine.open_position(pos)

                    elif check_long_entry(bias, price, low_5m, delta_5m, buy_pct_5m, has_large_sells_recent(), engine.state):
                        pos = create_position("LONG", price, engine.state, nearest_short_liq_price, "Long entry signal")
                        engine.open_position(pos)

                # Update dashboard
                dashboard.current_price = price
                dashboard.unrealized_pnl = engine.get_unrealized_pnl(price)
                dashboard.delta_5m = delta_5m
                dashboard.buy_pressure = buy_pct_5m
                dashboard.vwap = vwap
                dashboard.high_5m = high_5m
                dashboard.low_5m = low_5m
                dashboard.today_trades = db.get_today_trades()
                dashboard.hl_connected = hl_feed.connected
                dashboard.binance_connected = True  # simplified
                dashboard.bybit_connected = True

            except Exception as e:
                logger.error(f"Strategy loop error: {e}")

            await asyncio.sleep(1)

    # Start all tasks
    ws_tasks = [
        asyncio.create_task(bybit.connect()),
        asyncio.create_task(binance.connect()),
        asyncio.create_task(hl_feed.connect()),
        asyncio.create_task(strategy_loop()),
    ]

    try:
        with dashboard.create_live() as live:
            while True:
                live.update(dashboard.render())
                await asyncio.sleep(0.5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for task in ws_tasks:
            task.cancel()
        await liq_client.close()
        db.close()


def main():
    try:
        asyncio.run(run_scalper())
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
