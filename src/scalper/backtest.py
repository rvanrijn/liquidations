"""Backtest engine for BTC scalper strategy.

Replays historical 1m Binance klines through the existing strategy functions.
Uses taker_buy_quote_volume for delta and buy pressure calculation.

Simplified bias: no historical liq data, uses delta_15m + VWAP alignment only.
"""

import argparse
import sys
from collections import deque
from datetime import datetime, timezone, timedelta

import aiohttp
import asyncio

from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.scalper.models import BotState, Position
from src.scalper.obv_macd import OBVMACDCalculator
from src.scalper.strategy import check_exit, check_short_entry, check_long_entry, create_position, Bias


# ---------------------------------------------------------------------------
# Binance kline fetching
# ---------------------------------------------------------------------------

KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"
KLINE_LIMIT = 1500  # max per request


async def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[list]:
    """Fetch klines from Binance Futures API, paginated.

    Each kline: [open_time, open, high, low, close, volume, close_time,
                  quote_volume, trades, taker_buy_base_vol, taker_buy_quote_vol, ...]
    """
    all_klines: list[list] = []
    current_start = start_ms

    async with aiohttp.ClientSession() as session:
        while current_start < end_ms:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": KLINE_LIMIT,
            }
            async with session.get(KLINE_URL, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Binance API error {resp.status}: {text}")
                data = await resp.json()

            if not data:
                break

            all_klines.extend(data)
            # Move start to 1ms after last kline's close_time
            current_start = int(data[-1][6]) + 1

            if len(data) < KLINE_LIMIT:
                break

    return all_klines


def parse_kline(raw: list) -> dict:
    """Parse a raw Binance kline into a dict."""
    return {
        "open_time": int(raw[0]),
        "open": float(raw[1]),
        "high": float(raw[2]),
        "low": float(raw[3]),
        "close": float(raw[4]),
        "volume": float(raw[5]),
        "close_time": int(raw[6]),
        "quote_volume": float(raw[7]),
        "trades": int(raw[8]),
        "taker_buy_base_vol": float(raw[9]),
        "taker_buy_quote_vol": float(raw[10]),
    }


# ---------------------------------------------------------------------------
# Rolling window helpers
# ---------------------------------------------------------------------------

class RollingWindow:
    """Accumulates 1m candles into N-minute windows for delta / high / low."""

    def __init__(self, window_minutes: int):
        self._window_ms = window_minutes * 60 * 1000
        self._candles: deque[dict] = deque()

    def add(self, candle: dict) -> None:
        self._candles.append(candle)
        cutoff = candle["open_time"] - self._window_ms
        while self._candles and self._candles[0]["open_time"] < cutoff:
            self._candles.popleft()

    def delta(self) -> float:
        """Taker buy - taker sell in quote volume."""
        buy = sum(c["taker_buy_quote_vol"] for c in self._candles)
        total = sum(c["quote_volume"] for c in self._candles)
        return buy - (total - buy)

    def buy_pressure(self) -> float:
        """Taker buy as pct of total quote volume."""
        total = sum(c["quote_volume"] for c in self._candles)
        if total == 0:
            return 50.0
        buy = sum(c["taker_buy_quote_vol"] for c in self._candles)
        return (buy / total) * 100

    def high(self) -> float:
        if not self._candles:
            return 0.0
        return max(c["high"] for c in self._candles)

    def low(self) -> float:
        if not self._candles:
            return 0.0
        return min(c["low"] for c in self._candles)

    def vwap(self) -> float:
        """Rolling VWAP: sum(typical_price * volume) / sum(volume)."""
        if not self._candles:
            return 0.0
        cum_pv = 0.0
        cum_vol = 0.0
        for c in self._candles:
            typical = (c["high"] + c["low"] + c["close"]) / 3
            cum_pv += typical * c["quote_volume"]
            cum_vol += c["quote_volume"]
        if cum_vol == 0.0:
            return 0.0
        return cum_pv / cum_vol


# ---------------------------------------------------------------------------
# Simplified bias (no liq data)
# ---------------------------------------------------------------------------

def calculate_backtest_bias(
    delta_15m: float,
    price: float,
    vwap: float,
) -> Bias:
    """Simplified bias using only delta_15m and VWAP alignment.

    No historical liq data available, so we skip the 2:1 liq ratio check.
    """
    price_vs_vwap = "ABOVE" if price >= vwap else "BELOW"

    if delta_15m < 0 and price <= vwap:
        return Bias(
            direction="SHORT",
            long_liq_usd=0.0,
            short_liq_usd=0.0,
            delta_15m=delta_15m,
            price_vs_vwap=price_vs_vwap,
        )

    if delta_15m > 0 and price >= vwap:
        return Bias(
            direction="LONG",
            long_liq_usd=0.0,
            short_liq_usd=0.0,
            delta_15m=delta_15m,
            price_vs_vwap=price_vs_vwap,
        )

    return Bias(
        direction="NONE",
        long_liq_usd=0.0,
        short_liq_usd=0.0,
        delta_15m=delta_15m,
        price_vs_vwap=price_vs_vwap,
    )


# ---------------------------------------------------------------------------
# Intra-candle exit check
# ---------------------------------------------------------------------------

def check_intra_candle_exit(
    position: Position,
    candle: dict,
    delta_5m: float,
    min_hold_ms: int = 5 * 60 * 1000,
    early_exit_profit_only: bool = True,
) -> tuple[str | None, float]:
    """Check if stop or TP was hit within the candle's high-low range.

    Priority: STOP first, then TP1/TP2, then EARLY_EXIT at close (after cooldown).

    Args:
        early_exit_profit_only: If True, EARLY_EXIT only fires when position is in profit.
            If False, fires on any delta flip after cooldown.

    Returns:
        (exit_reason, exit_price) or (None, 0.0) to hold.
    """
    high = candle["high"]
    low = candle["low"]
    close = candle["close"]
    held_ms = candle["open_time"] - position.entry_time

    if position.side == "SHORT":
        # STOP hit if high >= stop_loss
        if high >= position.stop_loss:
            return "STOP", position.stop_loss

        # TP1 hit if low <= tp1
        if low <= position.tp1 and not position.tp1_hit:
            return "TP1", position.tp1

        # TP2 hit if low <= tp2 (after TP1)
        if low <= position.tp2 and position.tp1_hit:
            return "TP2", position.tp2

        # EARLY_EXIT at close if delta flipped
        if held_ms >= min_hold_ms and delta_5m > 0:
            if not early_exit_profit_only or close < position.entry_price:
                return "EARLY_EXIT", close

    else:  # LONG
        # STOP hit if low <= stop_loss
        if low <= position.stop_loss:
            return "STOP", position.stop_loss

        # TP1 hit if high >= tp1
        if high >= position.tp1 and not position.tp1_hit:
            return "TP1", position.tp1

        # TP2 hit if high >= tp2 (after TP1)
        if high >= position.tp2 and position.tp1_hit:
            return "TP2", position.tp2

        # EARLY_EXIT at close if delta flipped
        if held_ms >= min_hold_ms and delta_5m < 0:
            if not early_exit_profit_only or close > position.entry_price:
                return "EARLY_EXIT", close

    return None, 0.0


# ---------------------------------------------------------------------------
# Trade record for backtest
# ---------------------------------------------------------------------------

class BacktestTrade:
    __slots__ = (
        "trade_num", "time_str", "side", "entry", "exit_price",
        "pnl", "r_multiple", "reason", "entry_time_ms",
    )

    def __init__(self, trade_num, time_str, side, entry, exit_price, pnl, r_multiple, reason, entry_time_ms):
        self.trade_num = trade_num
        self.time_str = time_str
        self.side = side
        self.entry = entry
        self.exit_price = exit_price
        self.pnl = pnl
        self.r_multiple = r_multiple
        self.reason = reason
        self.entry_time_ms = entry_time_ms


# ---------------------------------------------------------------------------
# Position management helpers (mirrors PaperEngine logic)
# ---------------------------------------------------------------------------

def close_position(
    state: BotState,
    exit_price: float,
    exit_reason: str,
    candle_time_ms: int,
    trades: list[BacktestTrade],
) -> None:
    """Close position and record trade. Mirrors PaperEngine.close_position."""
    pos = state.position
    if pos is None:
        return

    if pos.side == "LONG":
        pnl = (exit_price - pos.entry_price) / pos.entry_price * pos.size_usd * pos.remaining_pct
    else:
        pnl = (pos.entry_price - exit_price) / pos.entry_price * pos.size_usd * pos.remaining_pct

    r_multiple = pnl / state.risk_usd if state.risk_usd > 0 else 0.0
    is_partial = exit_reason == "TP1" and not pos.tp1_hit

    state.account_balance += pnl
    state.daily_pnl += pnl

    if is_partial:
        pos.tp1_hit = True
        pos.remaining_pct = 0.5
        pos.stop_loss = pos.entry_price  # breakeven stop
    else:
        state.daily_trades += 1
        state.total_trades += 1
        if pnl > 0:
            state.wins += 1
        else:
            state.losses += 1

        time_str = datetime.fromtimestamp(
            pos.entry_time / 1000, tz=timezone.utc
        ).strftime("%b %d %H:%M")

        trades.append(BacktestTrade(
            trade_num=len(trades) + 1,
            time_str=time_str,
            side=pos.side,
            entry=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            r_multiple=r_multiple,
            reason=exit_reason,
            entry_time_ms=pos.entry_time,
        ))

        state.position = None

        # Check daily loss limit
        if state.risk_usd > 0:
            daily_r = state.daily_pnl / state.risk_usd
            if daily_r <= state.max_daily_loss_r:
                state.is_active = False


def reset_daily(state: BotState, current_day: str, last_day: list[str]) -> None:
    """Reset daily counters on day boundary."""
    if current_day != last_day[0]:
        state.daily_pnl = 0.0
        state.daily_trades = 0
        state.is_active = True
        last_day[0] = current_day


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

async def run_backtest(start_dt: datetime, end_dt: datetime, weekdays_only: bool = False, no_obv: bool = False) -> None:
    console = Console()

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    days = (end_dt - start_dt).days

    console.print(f"\n[bold]BTC Scalper Backtest: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')} ({days} days)[/bold]")
    console.print("Fetching 1m klines from Binance Futures...", style="dim")

    raw_klines = await fetch_klines("BTCUSDT", "1m", start_ms, end_ms)
    candles = [parse_kline(k) for k in raw_klines]

    if not candles:
        console.print("[red]No kline data returned. Check date range.[/red]")
        return

    console.print(f"Loaded {len(candles):,} candles\n", style="dim")

    # Init indicators
    obv_macd = OBVMACDCalculator(fast_period=9, slow_period=26, signal_period=2, obv_smooth=10, candle_minutes=1)
    # VWAP comes from window_5m.vwap() — no separate calculator needed
    window_5m = RollingWindow(5)
    window_15m = RollingWindow(15)

    # State
    state = BotState()
    trades: list[BacktestTrade] = []
    last_day = [""]
    warmup_skipped = 0
    last_trade_close_ms = 0  # cooldown between trades
    cooldown_ms = 50 * 60 * 1000  # 50 minutes
    equity_peak = state.account_balance
    max_drawdown = 0.0
    equity_curve: list[float] = []

    for candle in candles:
        # Day reset
        day_str = datetime.fromtimestamp(candle["open_time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        reset_daily(state, day_str, last_day)

        # Update indicators
        obv_macd.update_candle(candle["close"], candle["volume"])
        window_5m.add(candle)
        window_15m.add(candle)

        # Skip during warmup
        if not obv_macd.ready:
            warmup_skipped += 1
            continue

        price = candle["close"]
        delta_5m = window_5m.delta()
        delta_15m = window_15m.delta()
        buy_pct = window_5m.buy_pressure()
        high_5m = window_5m.high()
        low_5m = window_5m.low()

        # Simplified bias (using 5m rolling VWAP)
        vwap_val = window_5m.vwap()
        bias = calculate_backtest_bias(delta_15m, price, vwap_val)

        # --- Check exits (intra-candle) ---
        if state.position is not None:
            reason, exit_price = check_intra_candle_exit(
                state.position, candle, delta_5m,
                min_hold_ms=15 * 60 * 1000, early_exit_profit_only=False,
            )
            if reason:
                close_position(state, exit_price, reason, candle["open_time"], trades)
                if state.position is None:
                    last_trade_close_ms = candle["open_time"]
                # If TP1 partial, check for TP2/STOP in same candle
                if state.position is not None:
                    reason2, exit_price2 = check_intra_candle_exit(
                        state.position, candle, delta_5m,
                        min_hold_ms=15 * 60 * 1000, early_exit_profit_only=False,
                    )
                    if reason2:
                        close_position(state, exit_price2, reason2, candle["open_time"], trades)
                        if state.position is None:
                            last_trade_close_ms = candle["open_time"]

        # --- Check entries (at close, with cooldown, session 0-12 UTC) ---
        candle_dt = datetime.fromtimestamp(candle["open_time"] / 1000, tz=timezone.utc)
        skip_day = candle_dt.weekday() in (3, 5, 6)  # Thu=3, Sat=5, Sun=6
        hour_utc = candle_dt.hour
        in_session = hour_utc < 12  # 0-12 UTC
        time_since_last = candle["open_time"] - last_trade_close_ms
        if state.position is None and state.is_active and time_since_last >= cooldown_ms and not skip_day and in_session:
            # TP2 approximation: use a fixed offset since no liq data
            tp2_short = price * 0.980  # -2.0% for short TP2
            tp2_long = price * 1.020   # +2.0% for long TP2

            obv_bear = True if no_obv else obv_macd.is_bearish
            obv_bull = True if no_obv else obv_macd.is_bullish

            if check_short_entry(bias, price, high_5m, delta_5m, buy_pct, obv_bear, state):
                pos = create_position("SHORT", price, state, tp2_short, "Backtest short")
                pos.entry_time = candle["open_time"]
                state.position = pos

            elif check_long_entry(bias, price, low_5m, delta_5m, buy_pct, obv_bull, state):
                pos = create_position("LONG", price, state, tp2_long, "Backtest long")
                pos.entry_time = candle["open_time"]
                state.position = pos

        # Track equity for drawdown
        equity = state.account_balance
        if state.position:
            # add unrealized
            p = state.position
            if p.side == "LONG":
                equity += (price - p.entry_price) / p.entry_price * p.size_usd * p.remaining_pct
            else:
                equity += (p.entry_price - price) / p.entry_price * p.size_usd * p.remaining_pct
        equity_curve.append(equity)
        if equity > equity_peak:
            equity_peak = equity
        dd = equity_peak - equity
        if dd > max_drawdown:
            max_drawdown = dd

    # Close any open position at last candle close
    if state.position is not None and candles:
        last_candle = candles[-1]
        close_position(state, last_candle["close"], "END", last_candle["open_time"], trades)

    # ---------------------------------------------------------------------------
    # Output results
    # ---------------------------------------------------------------------------

    console.print(f"OBV MACD warmup: {warmup_skipped} candles skipped\n", style="dim")

    if not trades:
        console.print("[yellow]No trades were generated in this period.[/yellow]")
        console.print("\nThis can happen when entry conditions are very strict.")
        console.print("Try a longer date range with --days or adjust entry filters.\n")
        return

    # Trades table
    table = Table(title="TRADES", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Time", width=14)
    table.add_column("Side", width=6)
    table.add_column("Entry", justify="right", width=10)
    table.add_column("Exit", justify="right", width=10)
    table.add_column("P&L", justify="right", width=10)
    table.add_column("R", justify="right", width=7)
    table.add_column("Reason", width=12)

    for t in trades:
        pnl_style = "green" if t.pnl >= 0 else "red"
        table.add_row(
            str(t.trade_num),
            t.time_str,
            t.side,
            f"${t.entry:,.0f}",
            f"${t.exit_price:,.0f}",
            Text(f"${t.pnl:+,.2f}", style=pnl_style),
            Text(f"{t.r_multiple:+.1f}R", style=pnl_style),
            t.reason,
        )

    console.print(table)

    # Summary stats
    total_pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    losses = sum(1 for t in trades if t.pnl <= 0)
    win_rate = (wins / len(trades)) * 100 if trades else 0
    avg_r = sum(t.r_multiple for t in trades) / len(trades) if trades else 0

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    console.print("\n[bold]SUMMARY[/bold]")
    console.print(f"  Total trades: {len(trades)}")
    console.print(f"  Wins: {wins} | Losses: {losses} | Win rate: {win_rate:.1f}%")

    pnl_style = "green" if total_pnl >= 0 else "red"
    console.print(f"  Total P&L: [{pnl_style}]${total_pnl:+,.2f}[/{pnl_style}]")
    console.print(f"  Avg R: {avg_r:+.1f}R")
    console.print(f"  Max drawdown: [red]-${max_drawdown:,.2f}[/red]")
    console.print(f"  Profit factor: {profit_factor:.2f}")

    # Exit reason breakdown
    reason_stats: dict[str, dict] = {}
    for t in trades:
        if t.reason not in reason_stats:
            reason_stats[t.reason] = {"count": 0, "pnl": 0.0, "r_sum": 0.0}
        reason_stats[t.reason]["count"] += 1
        reason_stats[t.reason]["pnl"] += t.pnl
        reason_stats[t.reason]["r_sum"] += t.r_multiple

    console.print("\n[bold]EXIT BREAKDOWN[/bold]")
    for reason, stats in sorted(reason_stats.items()):
        avg_r = stats["r_sum"] / stats["count"] if stats["count"] > 0 else 0
        pnl_style = "green" if stats["pnl"] >= 0 else "red"
        console.print(
            f"  {reason:12s}: {stats['count']:3d} trades, "
            f"avg {avg_r:+.2f}R, "
            f"total [{pnl_style}]${stats['pnl']:+,.2f}[/{pnl_style}]"
        )

    # Day-of-week breakdown
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_stats: dict[int, dict] = {i: {"count": 0, "pnl": 0.0} for i in range(7)}
    for t in trades:
        dow = datetime.fromtimestamp(t.entry_time_ms / 1000, tz=timezone.utc).weekday()
        day_stats[dow]["count"] += 1
        day_stats[dow]["pnl"] += t.pnl

    console.print("\n[bold]DAY OF WEEK[/bold]")
    for dow in range(7):
        s = day_stats[dow]
        if s["count"] == 0:
            continue
        avg = s["pnl"] / s["count"]
        pnl_style = "green" if s["pnl"] >= 0 else "red"
        marker = " (weekend)" if dow >= 5 else ""
        console.print(
            f"  {day_names[dow]:3s}: {s['count']:3d} trades, "
            f"total [{pnl_style}]${s['pnl']:+,.2f}[/{pnl_style}], "
            f"avg [{pnl_style}]${avg:+,.2f}[/{pnl_style}]{marker}"
        )

    console.print(
        "\n[dim]Note: Bias simplified (no historical liq data) — live results may differ.[/dim]\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BTC scalper backtest engine")
    parser.add_argument("--days", type=int, default=7, help="Number of days to backtest (default: 7)")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD (overrides --days)")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD (default: now)")
    parser.add_argument("--weekdays", action="store_true", help="Only open new trades on weekdays (Mon-Fri)")
    parser.add_argument("--no-obv", action="store_true", dest="no_obv", help="Disable OBV MACD filter (for A/B testing)")
    parser.add_argument("--stop", type=float, default=None, help="Override stop %% (e.g. 0.25)")
    parser.add_argument("--tp1", type=float, default=None, help="Override TP1 %% (e.g. 1.0)")
    parser.add_argument("--tp2", type=float, default=None, help="Override TP2 %% (e.g. 1.2)")
    parser.add_argument("--cooldown", type=int, default=None, help="Override cooldown minutes (e.g. 15)")
    parser.add_argument("--hold", type=int, default=None, help="Override min hold minutes (e.g. 5)")
    parser.add_argument("--sweep", action="store_true", help="Run parameter sweep (compact output)")
    return parser.parse_args(argv)


async def run_sweep(start_dt: datetime, end_dt: datetime) -> None:
    """Run parameter sweep at monthly level: download per month, test combos, aggregate."""
    console = Console()

    # Build list of months in range
    months: list[tuple[datetime, datetime]] = []
    cur = start_dt.replace(day=1)
    while cur < end_dt:
        next_month = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = min(next_month, end_dt)
        months.append((cur, month_end))
        cur = next_month

    console.print(f"\n[bold]Parameter Sweep: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')} ({len(months)} months)[/bold]")

    # Download candles per month
    monthly_candles: list[tuple[str, list[dict]]] = []
    for m_start, m_end in months:
        label = m_start.strftime("%b %Y")
        console.print(f"  Fetching {label}...", style="dim", end=" ")
        raw = await fetch_klines("BTCUSDT", "1m", int(m_start.timestamp() * 1000), int(m_end.timestamp() * 1000))
        candles = [parse_kline(k) for k in raw]
        monthly_candles.append((label, candles))
        console.print(f"{len(candles):,} candles", style="dim")

    # Pre-compute day_num and weekday on each candle for speed
    for _, candles in monthly_candles:
        for c in candles:
            day_num = c["open_time"] // 86_400_000
            c["_day_num"] = day_num
            c["_weekday"] = datetime.fromtimestamp(c["open_time"] / 1000, tz=timezone.utc).weekday()

    # Define grid
    stop_vals = [0.25, 0.30, 0.35, 0.40]
    tp1_vals = [0.8, 1.0, 1.2, 1.5]
    tp2_vals = [1.2, 1.5, 2.0]
    early_modes = [False, True]
    regime_filters = [
        (0.0, 999.0),     # no filter
        (0.03, 999.0),    # skip dead
        (0.03, 0.20),     # moderate
        (0.05, 0.25),     # active controlled
    ]

    configs = []
    for stop_pct in stop_vals:
        for tp1_pct in tp1_vals:
            for tp2_pct in tp2_vals:
                if tp2_pct <= tp1_pct:
                    continue
                for ep in early_modes:
                    for atr_min, atr_max in regime_filters:
                        configs.append((stop_pct, tp1_pct, tp2_pct, ep, atr_min, atr_max))

    console.print(f"\nTesting {len(configs)} configs x {len(months)} months = {len(configs) * len(months)} runs...", style="dim")

    # Run sweep: for each config, run all months, sum P&L
    agg_results = []
    for i, (stop_pct, tp1_pct, tp2_pct, ep, atr_min, atr_max) in enumerate(configs):
        total_pnl = 0.0
        total_trades = 0
        total_wins = 0
        green_months = 0
        month_pnls = []

        for label, candles in monthly_candles:
            r = _run_candles_fast(
                candles, stop_pct, tp1_pct, tp2_pct,
                cooldown_min=15, hold_min=5,
                early_exit_profit_only=ep,
                atr_min=atr_min, atr_max=atr_max,
            )
            total_pnl += r["pnl"]
            total_trades += r["trades"]
            total_wins += r["wins"]
            month_pnls.append(r["pnl"])
            if r["pnl"] > 0:
                green_months += 1

        wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
        gross_p = sum(p for p in month_pnls if p > 0)
        gross_l = abs(sum(p for p in month_pnls if p < 0))
        pf = gross_p / gross_l if gross_l > 0 else float("inf")

        agg_results.append({
            "stop": stop_pct, "tp1": tp1_pct, "tp2": tp2_pct,
            "early_profit": ep, "atr_min": atr_min, "atr_max": atr_max,
            "trades": total_trades, "wr": wr, "pnl": total_pnl,
            "pf": pf, "green": green_months, "months": month_pnls,
        })

        if (i + 1) % 50 == 0:
            console.print(f"  {i + 1}/{len(configs)} configs done...", style="dim")

    console.print(f"Done! {len(configs)} configs tested.\n", style="dim")

    # Sort by total P&L
    agg_results.sort(key=lambda x: x["pnl"], reverse=True)

    # Top 25 table
    table = Table(title="SWEEP RESULTS (top 25 by annual P&L)")
    table.add_column("Stop%", width=5)
    table.add_column("TP1", width=4)
    table.add_column("TP2", width=4)
    table.add_column("Exit", width=7)
    table.add_column("ATR", width=10)
    table.add_column("#", justify="right", width=5)
    table.add_column("Win%", justify="right", width=5)
    table.add_column("P&L", justify="right", width=10)
    table.add_column("PF", justify="right", width=5)
    table.add_column("Green", justify="right", width=5)

    for r in agg_results[:25]:
        pnl_style = "green" if r["pnl"] >= 0 else "red"
        wr_style = "green" if r["wr"] >= 50 else "white"
        exit_mode = "profit" if r["early_profit"] else "normal"
        atr_str = "none" if r["atr_min"] == 0 and r["atr_max"] >= 999 else f"{r['atr_min']:.2f}-{r['atr_max']:.2f}"
        gm_style = "green" if r["green"] >= 8 else "yellow" if r["green"] >= 6 else "red"
        table.add_row(
            f"{r['stop']:.2f}",
            f"{r['tp1']:.1f}",
            f"{r['tp2']:.1f}",
            exit_mode,
            atr_str,
            str(r["trades"]),
            f"[{wr_style}]{r['wr']:.1f}[/{wr_style}]",
            f"[{pnl_style}]${r['pnl']:+,.0f}[/{pnl_style}]",
            f"{r['pf']:.2f}",
            f"[{gm_style}]{r['green']}/12[/{gm_style}]",
        )

    console.print(table)

    # Show monthly breakdown for top 3
    console.print("\n[bold]Monthly P&L for top 3 configs:[/bold]")
    month_labels = [label for label, _ in monthly_candles]
    for rank, r in enumerate(agg_results[:3], 1):
        exit_mode = "profit-only" if r["early_profit"] else "normal"
        atr_str = "no filter" if r["atr_min"] == 0 and r["atr_max"] >= 999 else f"ATR {r['atr_min']:.2f}-{r['atr_max']:.2f}"
        console.print(f"\n  #{rank}: Stop {r['stop']:.2f}% | TP1 {r['tp1']:.1f}% | TP2 {r['tp2']:.1f}% | {exit_mode} | {atr_str}")
        parts = []
        for label, pnl in zip(month_labels, r["months"]):
            style = "green" if pnl >= 0 else "red"
            parts.append(f"[{style}]{label[:3]}:{pnl:+.0f}[/{style}]")
        console.print(f"  {' | '.join(parts)}")

    # Phase 2: Take top 3 base configs, test session/cooldown/max_daily
    console.print("\n[bold]Phase 2: Session/Cooldown/MaxDaily sweep...[/bold]")
    top3_bases = agg_results[:3]
    phase2_results = []
    p2_count = 0

    for base in top3_bases:
        for sess_start, sess_end in [(0, 24), (0, 12), (0, 8), (8, 20), (0, 16)]:
            for cd in [15, 20, 30, 45, 60]:
                for md in [3, 4, 6]:
                    if sess_start == 0 and sess_end == 24 and cd == 15 and md == 6:
                        continue  # already have baseline

                    total_pnl = 0.0
                    total_trades = 0
                    total_wins = 0
                    green_months = 0
                    month_pnls = []

                    for label, m_candles in monthly_candles:
                        r = _run_candles_fast(
                            m_candles, base["stop"], base["tp1"], base["tp2"],
                            cooldown_min=cd, hold_min=5,
                            early_exit_profit_only=base["early_profit"],
                            atr_min=base["atr_min"], atr_max=base["atr_max"],
                            session_start=sess_start, session_end=sess_end,
                            max_daily=md,
                        )
                        total_pnl += r["pnl"]
                        total_trades += r["trades"]
                        total_wins += r["wins"]
                        month_pnls.append(r["pnl"])
                        if r["pnl"] > 0:
                            green_months += 1

                    wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
                    phase2_results.append({
                        "stop": base["stop"], "tp1": base["tp1"], "tp2": base["tp2"],
                        "early_profit": base["early_profit"],
                        "atr_min": base["atr_min"], "atr_max": base["atr_max"],
                        "sess": f"{sess_start}-{sess_end}",
                        "cd": cd, "md": md,
                        "trades": total_trades, "wr": wr, "pnl": total_pnl,
                        "green": green_months, "months": month_pnls,
                    })
                    p2_count += 1

        console.print(f"  Base config done ({p2_count} combos)...", style="dim")

    phase2_results.sort(key=lambda x: x["pnl"], reverse=True)

    table2 = Table(title="PHASE 2: Best configs with filters (top 25)")
    table2.add_column("Base", width=16)
    table2.add_column("Sess", width=5)
    table2.add_column("CD", width=3)
    table2.add_column("MD", width=3)
    table2.add_column("#", justify="right", width=5)
    table2.add_column("Win%", justify="right", width=5)
    table2.add_column("P&L", justify="right", width=10)
    table2.add_column("Green", justify="right", width=5)

    for r in phase2_results[:25]:
        pnl_style = "green" if r["pnl"] >= 0 else "red"
        wr_style = "green" if r["wr"] >= 50 else "white"
        gm_style = "green" if r["green"] >= 8 else "yellow" if r["green"] >= 6 else "red"
        base_str = f"{r['stop']:.2f}/{r['tp1']:.1f}/{r['tp2']:.1f}"
        table2.add_row(
            base_str,
            r["sess"],
            str(r["cd"]),
            str(r["md"]),
            str(r["trades"]),
            f"[{wr_style}]{r['wr']:.1f}[/{wr_style}]",
            f"[{pnl_style}]${r['pnl']:+,.0f}[/{pnl_style}]",
            f"[{gm_style}]{r['green']}/12[/{gm_style}]",
        )

    console.print(table2)

    # Monthly breakdown for best overall
    if phase2_results and phase2_results[0]["pnl"] > agg_results[0]["pnl"]:
        best = phase2_results[0]
        console.print(f"\n[bold]BEST config monthly P&L:[/bold]")
        exit_mode = "profit-only" if best["early_profit"] else "normal"
        atr_str = f"ATR {best['atr_min']:.2f}-{best['atr_max']:.2f}"
        console.print(f"  Stop {best['stop']:.2f}% | TP1 {best['tp1']:.1f}% | TP2 {best['tp2']:.1f}% | {exit_mode} | {atr_str} | Sess {best['sess']} UTC | CD {best['cd']}m | Max {best['md']}/day")
        parts = []
        for label, pnl in zip(month_labels, best["months"]):
            style = "green" if pnl >= 0 else "red"
            parts.append(f"[{style}]{label[:3]}:{pnl:+.0f}[/{style}]")
        console.print(f"  {' | '.join(parts)}")

    console.print(f"\nPhase 2 combos: {p2_count}", style="dim")

    # Phase 3: Focused micro-sweep around the very best config
    if phase2_results:
        best = phase2_results[0]
        console.print("\n[bold]Phase 3: Micro-sweep around best config...[/bold]")
        micro_results = []
        # Fixed base: use best stop/tp, vary session/cooldown/maxdaily/hold
        stop_tp_combos = [
            (best["stop"], best["tp1"], best["tp2"]),
            (best["stop"] - 0.05, best["tp1"], best["tp2"]),
            (best["stop"] + 0.05, best["tp1"], best["tp2"]),
            (best["stop"], best["tp1"] + 0.3, best["tp2"] + 0.5),
            (best["stop"], best["tp1"], best["tp2"] + 0.5),
        ]
        for stop_pct, tp1_pct, tp2_pct in stop_tp_combos:
            if stop_pct <= 0 or tp2_pct <= tp1_pct:
                continue
            for cd in [30, 40, 45, 50, 60, 90, 120]:
                for md in [2, 3, 4]:
                    for sess_s, sess_e in [(0, 10), (0, 12), (0, 14), (2, 12), (2, 14), (0, 8)]:
                        for hold in [5, 10, 15]:
                            total_pnl = 0.0
                            total_trades = 0
                            total_wins = 0
                            green_months = 0
                            month_pnls = []
                            for label, m_candles in monthly_candles:
                                r = _run_candles_fast(
                                    m_candles, stop_pct, tp1_pct, tp2_pct,
                                    cooldown_min=cd, hold_min=hold,
                                    early_exit_profit_only=best["early_profit"],
                                    atr_min=best["atr_min"], atr_max=best["atr_max"],
                                    session_start=sess_s, session_end=sess_e,
                                    max_daily=md,
                                )
                                total_pnl += r["pnl"]
                                total_trades += r["trades"]
                                total_wins += r["wins"]
                                month_pnls.append(r["pnl"])
                                if r["pnl"] > 0:
                                    green_months += 1
                            wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
                            micro_results.append({
                                "stop": stop_pct, "tp1": tp1_pct, "tp2": tp2_pct,
                                "sess": f"{sess_s}-{sess_e}", "cd": cd, "md": md, "hold": hold,
                                "trades": total_trades, "wr": wr, "pnl": total_pnl,
                                "green": green_months, "months": month_pnls,
                            })

        micro_results.sort(key=lambda x: x["pnl"], reverse=True)
        console.print(f"  Tested {len(micro_results)} micro-configs", style="dim")

        table3 = Table(title="PHASE 3: Micro-sweep (top 20)")
        table3.add_column("Stop", width=5)
        table3.add_column("TP1", width=4)
        table3.add_column("TP2", width=4)
        table3.add_column("Sess", width=5)
        table3.add_column("CD", width=3)
        table3.add_column("MD", width=3)
        table3.add_column("Hld", width=3)
        table3.add_column("#", justify="right", width=4)
        table3.add_column("Win%", justify="right", width=5)
        table3.add_column("P&L", justify="right", width=10)
        table3.add_column("Green", justify="right", width=5)

        for r in micro_results[:20]:
            pnl_style = "green" if r["pnl"] >= 0 else "red"
            wr_style = "green" if r["wr"] >= 50 else "white"
            gm_style = "green" if r["green"] >= 8 else "yellow" if r["green"] >= 6 else "red"
            table3.add_row(
                f"{r['stop']:.2f}",
                f"{r['tp1']:.1f}",
                f"{r['tp2']:.1f}",
                r["sess"],
                str(r["cd"]),
                str(r["md"]),
                str(r["hold"]),
                str(r["trades"]),
                f"[{wr_style}]{r['wr']:.1f}[/{wr_style}]",
                f"[{pnl_style}]${r['pnl']:+,.0f}[/{pnl_style}]",
                f"[{gm_style}]{r['green']}/12[/{gm_style}]",
            )

        console.print(table3)

        # Show monthly for top 3
        for rank, r in enumerate(micro_results[:3], 1):
            parts = []
            for label, pnl in zip(month_labels, r["months"]):
                style = "green" if pnl >= 0 else "red"
                parts.append(f"[{style}]{label[:3]}:{pnl:+.0f}[/{style}]")
            console.print(f"\n  #{rank}: {' | '.join(parts)}")

    console.print()


def _run_candles_fast(
    candles: list[dict],
    stop_pct: float,
    tp1_pct: float,
    tp2_pct: float,
    cooldown_min: int = 15,
    hold_min: int = 5,
    early_exit_profit_only: bool = True,
    atr_min: float = 0.0,
    atr_max: float = 999.0,
    session_start: int = 0,
    session_end: int = 24,
    max_daily: int = 6,
) -> dict:
    """Fast version of _run_candles for sweep — minimal overhead, returns wins count.

    Args:
        session_start/session_end: UTC hour range for entries (e.g. 0-12 = Asian+EU).
        max_daily: Max trades per day.
    """
    obv_macd = OBVMACDCalculator(fast_period=9, slow_period=26, signal_period=2, obv_smooth=10, candle_minutes=1)
    window_5m = RollingWindow(5)
    window_15m = RollingWindow(15)

    state = BotState()
    state.max_stop_pct = stop_pct / 100
    state.max_daily_trades = max_daily
    trades: list[BacktestTrade] = []
    last_day_num = -1
    last_trade_close_ms = 0
    cooldown_ms = cooldown_min * 60 * 1000
    hold_ms = hold_min * 60 * 1000

    use_regime = atr_min > 0 or atr_max < 999
    use_session = session_start != 0 or session_end != 24
    atr_buf: deque[float] = deque(maxlen=60)

    stop_mult_short = 1 + stop_pct / 100
    stop_mult_long = 1 - stop_pct / 100
    tp1_mult_short = 1 - tp1_pct / 100
    tp1_mult_long = 1 + tp1_pct / 100
    tp2_mult_short = 1 - tp2_pct / 100
    tp2_mult_long = 1 + tp2_pct / 100

    for candle in candles:
        # Fast day reset using pre-computed _day_num
        day_num = candle.get("_day_num", candle["open_time"] // 86_400_000)
        if day_num != last_day_num:
            state.daily_pnl = 0.0
            state.daily_trades = 0
            state.is_active = True
            last_day_num = day_num

        obv_macd.update_candle(candle["close"], candle["volume"])
        window_5m.add(candle)
        window_15m.add(candle)

        if use_regime:
            atr_buf.append(candle["high"] - candle["low"])

        if not obv_macd.ready:
            continue

        price = candle["close"]
        delta_5m = window_5m.delta()
        delta_15m = window_15m.delta()
        buy_pct = window_5m.buy_pressure()
        high_5m = window_5m.high()
        low_5m = window_5m.low()
        vwap_val = window_5m.vwap()
        bias = calculate_backtest_bias(delta_15m, price, vwap_val)

        # Exits (always check)
        if state.position is not None:
            reason, exit_price = check_intra_candle_exit(
                state.position, candle, delta_5m,
                min_hold_ms=hold_ms, early_exit_profit_only=early_exit_profit_only,
            )
            if reason:
                close_position(state, exit_price, reason, candle["open_time"], trades)
                if state.position is None:
                    last_trade_close_ms = candle["open_time"]
                if state.position is not None:
                    reason2, exit_price2 = check_intra_candle_exit(
                        state.position, candle, delta_5m,
                        min_hold_ms=hold_ms, early_exit_profit_only=early_exit_profit_only,
                    )
                    if reason2:
                        close_position(state, exit_price2, reason2, candle["open_time"], trades)
                        if state.position is None:
                            last_trade_close_ms = candle["open_time"]

        # Regime check
        regime_ok = True
        if use_regime and len(atr_buf) >= 60:
            atr_1h = sum(atr_buf) / len(atr_buf)
            atr_pct = (atr_1h / price) * 100 if price > 0 else 0
            regime_ok = atr_min <= atr_pct <= atr_max

        # Session check
        session_ok = True
        if use_session:
            hour_utc = (candle["open_time"] // 3_600_000) % 24
            session_ok = session_start <= hour_utc < session_end

        # Entries
        weekday = candle.get("_weekday", -1)
        if weekday == -1:
            weekday = datetime.fromtimestamp(candle["open_time"] / 1000, tz=timezone.utc).weekday()
        skip_day = weekday in (3, 5, 6)
        time_since_last = candle["open_time"] - last_trade_close_ms

        if (state.position is None and state.is_active and time_since_last >= cooldown_ms
                and not skip_day and regime_ok and session_ok):
            tp2_short = price * tp2_mult_short
            tp2_long = price * tp2_mult_long

            if check_short_entry(bias, price, high_5m, delta_5m, buy_pct, obv_macd.is_bearish, state):
                pos = Position(
                    side="SHORT", entry_price=price, size_usd=state.position_size_usd,
                    leverage=state.leverage, stop_loss=price * stop_mult_short,
                    tp1=price * tp1_mult_short, tp2=tp2_short, entry_reason="sweep",
                )
                pos.entry_time = candle["open_time"]
                state.position = pos
            elif check_long_entry(bias, price, low_5m, delta_5m, buy_pct, obv_macd.is_bullish, state):
                pos = Position(
                    side="LONG", entry_price=price, size_usd=state.position_size_usd,
                    leverage=state.leverage, stop_loss=price * stop_mult_long,
                    tp1=price * tp1_mult_long, tp2=tp2_long, entry_reason="sweep",
                )
                pos.entry_time = candle["open_time"]
                state.position = pos

    # Close open position
    if state.position is not None and candles:
        close_position(state, candles[-1]["close"], "END", candles[-1]["open_time"], trades)

    wins = sum(1 for t in trades if t.pnl > 0)
    total_pnl = sum(t.pnl for t in trades)

    return {
        "trades": len(trades), "wins": wins, "pnl": total_pnl,
    }


def _run_candles(
    candles: list[dict],
    stop_pct: float,
    tp1_pct: float,
    tp2_pct: float,
    cooldown_min: int = 15,
    hold_min: int = 5,
    early_exit_profit_only: bool = True,
    atr_min: float = 0.0,
    atr_max: float = 999.0,
) -> dict:
    """Run backtest on pre-loaded candles with given parameters. Returns summary dict.

    Args:
        early_exit_profit_only: If True, EARLY_EXIT only when in profit. If False, any delta flip.
        atr_min: Minimum 1h ATR% to allow new entries (regime filter). 0 = no filter.
        atr_max: Maximum 1h ATR% to allow new entries. 999 = no filter.
    """
    obv_macd = OBVMACDCalculator(fast_period=9, slow_period=26, signal_period=2, obv_smooth=10, candle_minutes=1)
    window_5m = RollingWindow(5)
    window_15m = RollingWindow(15)

    state = BotState()
    state.max_stop_pct = stop_pct / 100  # convert from % to decimal
    trades: list[BacktestTrade] = []
    last_day = [""]
    last_trade_close_ms = 0
    cooldown_ms = cooldown_min * 60 * 1000
    hold_ms = hold_min * 60 * 1000
    equity_peak = state.account_balance
    max_drawdown = 0.0

    # Regime filter: rolling 1h ATR (60 candle ranges)
    use_regime = atr_min > 0 or atr_max < 999
    atr_buf: deque[float] = deque(maxlen=60)

    # Precompute stop/tp multipliers
    stop_mult_short = 1 + stop_pct / 100
    stop_mult_long = 1 - stop_pct / 100
    tp1_mult_short = 1 - tp1_pct / 100
    tp1_mult_long = 1 + tp1_pct / 100
    tp2_mult_short = 1 - tp2_pct / 100
    tp2_mult_long = 1 + tp2_pct / 100

    for candle in candles:
        day_str = datetime.fromtimestamp(candle["open_time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        reset_daily(state, day_str, last_day)

        obv_macd.update_candle(candle["close"], candle["volume"])
        window_5m.add(candle)
        window_15m.add(candle)

        # Regime filter: track 1h ATR
        if use_regime:
            atr_buf.append(candle["high"] - candle["low"])

        if not obv_macd.ready:
            continue

        price = candle["close"]
        delta_5m = window_5m.delta()
        delta_15m = window_15m.delta()
        buy_pct = window_5m.buy_pressure()
        high_5m = window_5m.high()
        low_5m = window_5m.low()
        vwap_val = window_5m.vwap()
        bias = calculate_backtest_bias(delta_15m, price, vwap_val)

        # Exits (always check, regime filter only gates entries)
        if state.position is not None:
            reason, exit_price = check_intra_candle_exit(
                state.position, candle, delta_5m,
                min_hold_ms=hold_ms, early_exit_profit_only=early_exit_profit_only,
            )
            if reason:
                close_position(state, exit_price, reason, candle["open_time"], trades)
                if state.position is None:
                    last_trade_close_ms = candle["open_time"]
                if state.position is not None:
                    reason2, exit_price2 = check_intra_candle_exit(
                        state.position, candle, delta_5m,
                        min_hold_ms=hold_ms, early_exit_profit_only=early_exit_profit_only,
                    )
                    if reason2:
                        close_position(state, exit_price2, reason2, candle["open_time"], trades)
                        if state.position is None:
                            last_trade_close_ms = candle["open_time"]

        # Regime filter check
        regime_ok = True
        if use_regime and len(atr_buf) >= 60:
            atr_1h = sum(atr_buf) / len(atr_buf)
            atr_pct = (atr_1h / price) * 100 if price > 0 else 0
            regime_ok = atr_min <= atr_pct <= atr_max

        # Entries (skip Thu/Sat/Sun)
        candle_dt = datetime.fromtimestamp(candle["open_time"] / 1000, tz=timezone.utc)
        skip_day = candle_dt.weekday() in (3, 5, 6)
        time_since_last = candle["open_time"] - last_trade_close_ms
        if state.position is None and state.is_active and time_since_last >= cooldown_ms and not skip_day and regime_ok:
            tp2_short = price * tp2_mult_short
            tp2_long = price * tp2_mult_long

            if check_short_entry(bias, price, high_5m, delta_5m, buy_pct, obv_macd.is_bearish, state):
                pos = Position(
                    side="SHORT", entry_price=price, size_usd=state.position_size_usd,
                    leverage=state.leverage, stop_loss=price * stop_mult_short,
                    tp1=price * tp1_mult_short, tp2=tp2_short, entry_reason="sweep",
                )
                pos.entry_time = candle["open_time"]
                state.position = pos

            elif check_long_entry(bias, price, low_5m, delta_5m, buy_pct, obv_macd.is_bullish, state):
                pos = Position(
                    side="LONG", entry_price=price, size_usd=state.position_size_usd,
                    leverage=state.leverage, stop_loss=price * stop_mult_long,
                    tp1=price * tp1_mult_long, tp2=tp2_long, entry_reason="sweep",
                )
                pos.entry_time = candle["open_time"]
                state.position = pos

        # Drawdown tracking
        equity = state.account_balance
        if state.position:
            p = state.position
            if p.side == "LONG":
                equity += (price - p.entry_price) / p.entry_price * p.size_usd * p.remaining_pct
            else:
                equity += (p.entry_price - price) / p.entry_price * p.size_usd * p.remaining_pct
        if equity > equity_peak:
            equity_peak = equity
        dd = equity_peak - equity
        if dd > max_drawdown:
            max_drawdown = dd

    # Close open position
    if state.position is not None and candles:
        close_position(state, candles[-1]["close"], "END", candles[-1]["open_time"], trades)

    if not trades:
        return {
            "stop": stop_pct, "tp1": tp1_pct, "tp2": tp2_pct,
            "early_profit": early_exit_profit_only, "atr_min": atr_min, "atr_max": atr_max,
            "trades": 0, "wr": 0, "pnl": 0, "pf": 0, "dd": 0,
        }

    wins = sum(1 for t in trades if t.pnl > 0)
    total_pnl = sum(t.pnl for t in trades)
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "stop": stop_pct, "tp1": tp1_pct, "tp2": tp2_pct,
        "early_profit": early_exit_profit_only, "atr_min": atr_min, "atr_max": atr_max,
        "trades": len(trades), "wr": (wins / len(trades)) * 100,
        "pnl": total_pnl, "pf": pf, "dd": max_drawdown,
    }


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.end:
        end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        end_dt = datetime.now(timezone.utc)

    if args.start:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        start_dt = end_dt - timedelta(days=args.days)

    if args.sweep:
        asyncio.run(run_sweep(start_dt, end_dt))
        return

    # Apply parameter overrides
    if args.stop or args.tp1 or args.tp2 or args.cooldown or args.hold:
        # Quick run with overrides using _run_candles
        asyncio.run(_run_with_overrides(start_dt, end_dt, args))
        return

    asyncio.run(run_backtest(start_dt, end_dt, weekdays_only=args.weekdays, no_obv=args.no_obv))


async def _run_with_overrides(start_dt: datetime, end_dt: datetime, args) -> None:
    console = Console()
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    console.print(f"\n[bold]BTC Scalper Backtest (custom params)[/bold]")
    console.print("Fetching klines...", style="dim")
    raw_klines = await fetch_klines("BTCUSDT", "1m", start_ms, end_ms)
    candles = [parse_kline(k) for k in raw_klines]
    console.print(f"Loaded {len(candles):,} candles", style="dim")

    r = _run_candles(
        candles,
        stop_pct=args.stop or 0.25,
        tp1_pct=args.tp1 or 1.0,
        tp2_pct=args.tp2 or 1.2,
        cooldown_min=args.cooldown or 15,
        hold_min=args.hold or 5,
    )
    pnl_style = "green" if r["pnl"] >= 0 else "red"
    console.print(f"\n  Stop: {r['stop']:.2f}% | TP1: {r['tp1']:.1f}% | TP2: {r['tp2']:.1f}%")
    console.print(f"  Trades: {r['trades']} | Win rate: {r['wr']:.1f}% | PF: {r['pf']:.2f}")
    console.print(f"  P&L: [{pnl_style}]${r['pnl']:+,.2f}[/{pnl_style}] | Max DD: -${r['dd']:,.2f}\n")


if __name__ == "__main__":
    main()
