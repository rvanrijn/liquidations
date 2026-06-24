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
from src.scalper.obv_macd import OBVMACDCalculator
from src.scalper.hyperliquid import HyperliquidPriceFeed
from src.scalper.strategy import calculate_bias, check_short_entry, check_long_entry, create_position, check_exit, evaluate_conditions
from src.scalper.paper_engine import PaperEngine
from src.scalper.database import TradeDatabase
from src.scalper.ui.dashboard import ScalperDashboard

COINS = ["BTC"]  # Only BTC for scalping

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


async def run_scalper():
    # 1. Create aggregators for 1m, 5m, and 15m
    agg_1m = OrderFlowAggregator(window_minutes=1)
    agg_5m = OrderFlowAggregator(window_minutes=5)
    agg_15m = OrderFlowAggregator(window_minutes=15)

    # 2. VWAP calculator + OBV MACD
    vwap_calc = VWAPCalculator()
    obv_macd = OBVMACDCalculator(fast_period=9, slow_period=26, signal_period=2, obv_smooth=10, candle_minutes=1)

    # 3. Rolling 5m high/low
    price_history_5m: deque = deque()  # (timestamp_ms, price) tuples

    # 4. Event handler for orderflow trades
    def on_trade(event):
        """Feed trades to aggregators, VWAP, and large trade tracker."""
        agg_1m.add_event(event)
        agg_5m.add_event(event)
        agg_15m.add_event(event)

        # Only BTC for VWAP and price tracking
        if event.coin == "BTC":
            vwap_calc.update(event.price, event.size)
            obv_macd.update(event.price, event.size)

            now = int(time() * 1000)
            price_history_5m.append((now, event.price))
            # Prune older than 5 minutes
            cutoff = now - 5 * 60 * 1000
            while price_history_5m and price_history_5m[0][0] < cutoff:
                price_history_5m.popleft()

    # 5. Create WebSocket clients
    bybit = BybitTradeClient(coins=COINS, on_event=on_trade)
    binance = BinanceTradeClient(coins=COINS, on_event=on_trade)

    # 6. Hyperliquid price feed
    hl_feed = HyperliquidPriceFeed(coin="BTC")

    # 7. Liq levels client
    liq_client = BinanceLiqClient()

    # 8. Paper engine + database
    db = TradeDatabase()
    engine = PaperEngine(db)

    # 9. Dashboard
    dashboard = ScalperDashboard(engine.state)

    def get_high_5m() -> float:
        if not price_history_5m:
            return 0.0
        return max(p for _, p in price_history_5m)

    def get_low_5m() -> float:
        if not price_history_5m:
            return 0.0
        return min(p for _, p in price_history_5m)

    # Strategy loop - runs every 1 second
    async def strategy_loop():
        liq_data = None
        liq_last_fetch = 0
        last_trade_close_ms = 0  # cooldown between trades
        cooldown_ms = 50 * 60 * 1000  # 50 minutes

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
                buy_1m, sell_1m, delta_1m = agg_1m.totals()
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

                # OBV MACD state
                obv_bullish = obv_macd.is_bullish
                obv_bearish = obv_macd.is_bearish
                if obv_macd.ready:
                    obv_val = f"{obv_macd.macd_line:+.1f}/{obv_macd.signal_line:+.1f}"
                else:
                    remaining = obv_macd.warmup_remaining
                    obv_val = f"~{remaining}m left"

                # Check exits first
                now_ms = int(time() * 1000)
                if engine.state.position:
                    exit_reason = check_exit(engine.state.position, price, delta_5m, now_ms=now_ms)
                    if exit_reason:
                        record = engine.close_position(price, exit_reason, market_snapshot)
                        if record:
                            logger.info(f"CLOSED: {record.exit_reason} PnL: ${record.pnl_usd:+.2f} ({record.r_multiple:+.1f}R)")
                            if engine.state.position is None:
                                last_trade_close_ms = now_ms

                # Check entries (only if no position, skip Thu/Sat/Sun, session 0-12 UTC)
                from datetime import datetime, timezone as tz
                now_dt = datetime.fromtimestamp(now, tz=tz.utc)
                trade_dow = now_dt.weekday()
                skip_day = trade_dow in (3, 5, 6)  # Thu, Sat, Sun
                hour_utc = now_dt.hour
                in_session = hour_utc < 12  # 0-12 UTC only
                time_since_last = now_ms - last_trade_close_ms
                if engine.state.position is None and engine.state.is_active and not skip_day and in_session and time_since_last >= cooldown_ms:
                    if check_short_entry(bias, price, high_5m, delta_5m, buy_pct_5m, obv_bearish, engine.state):
                        pos = create_position("SHORT", price, engine.state, nearest_long_liq_price, "Short entry signal")
                        engine.open_position(pos)

                    elif check_long_entry(bias, price, low_5m, delta_5m, buy_pct_5m, obv_bullish, engine.state):
                        pos = create_position("LONG", price, engine.state, nearest_short_liq_price, "Long entry signal")
                        engine.open_position(pos)

                # Evaluate entry conditions for dashboard
                short_conds, long_conds = evaluate_conditions(
                    bias, price, high_5m, low_5m, delta_5m, buy_pct_5m,
                    obv_bullish, obv_bearish, obv_val, engine.state,
                )

                # Update dashboard
                dashboard.current_price = price
                dashboard.unrealized_pnl = engine.get_unrealized_pnl(price)
                dashboard.delta_1m = delta_1m
                dashboard.delta_5m = delta_5m
                dashboard.buy_pressure = buy_pct_5m
                dashboard.vwap = vwap
                dashboard.high_5m = high_5m
                dashboard.low_5m = low_5m
                dashboard.short_conds = short_conds
                dashboard.long_conds = long_conds
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
