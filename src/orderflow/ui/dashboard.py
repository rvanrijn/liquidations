from datetime import datetime
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.align import Align

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.orderflow.aggregator import OrderFlowAggregator
    from src.orderflow.models import TradeEvent


def format_usd(value: float) -> str:
    """Format USD value with K/M suffix."""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.0f}"


def format_time(timestamp_ms: int) -> str:
    """Format timestamp to HH:MM:SS."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    return dt.strftime("%H:%M:%S")


def format_delta(delta: float) -> Text:
    """Show +$X or -$X with green/red color."""
    if delta >= 0:
        return Text(f"+{format_usd(delta)}", style="green")
    else:
        return Text(f"-{format_usd(abs(delta))}", style="red")


def get_bias(delta: float) -> Text:
    """Get bias indicator based on delta."""
    if delta > 0:
        return Text("BULLISH", style="bold green")
    elif delta < 0:
        return Text("BEARISH", style="bold red")
    else:
        return Text("NEUTRAL", style="dim")


def format_pressure(buy_pct: float) -> Text:
    """Format buy pressure as percentage with color."""
    if buy_pct >= 60:
        style = "bold green"
    elif buy_pct >= 50:
        style = "green"
    elif buy_pct >= 40:
        style = "red"
    else:
        style = "bold red"
    return Text(f"{buy_pct:.0f}%", style=style)


def build_pressure_bar(buy_pct: float, width: int = 20) -> Text:
    """Build a mini pressure bar."""
    if buy_pct == 0 and width > 0:
        return Text("─" * width, style="dim")

    buy_chars = int(width * buy_pct / 100)
    sell_chars = width - buy_chars

    bar = Text()
    bar.append("█" * buy_chars, style="green")
    bar.append("█" * sell_chars, style="red")
    return bar


def build_timeframe_table(aggregators: dict[str, "OrderFlowAggregator"]) -> Table:
    """Build the timeframe comparison table."""
    table = Table(title="ORDER FLOW BY TIMEFRAME", expand=True)
    table.add_column("TF", width=6, style="cyan")
    table.add_column("Buy Pressure", justify="center", width=22)
    table.add_column("Delta", justify="right", width=12)
    table.add_column("Bias", justify="center", width=10)

    for tf in ["5m", "15m", "1h", "4h"]:
        agg = aggregators.get(tf)
        if not agg:
            continue

        buy_pct, _ = agg.buy_sell_pressure()
        buy, sell, delta = agg.totals()

        # Build pressure display with bar
        pressure_text = Text()
        pressure_text.append_text(format_pressure(buy_pct))
        pressure_text.append(" ")
        pressure_text.append_text(build_pressure_bar(buy_pct, width=14))

        table.add_row(
            tf.upper(),
            pressure_text,
            format_delta(delta),
            get_bias(delta),
        )

    return table


def build_coin_table(aggregator: "OrderFlowAggregator") -> Table:
    """Build the coin breakdown table."""
    table = Table(title="ORDER FLOW BY COIN (15M)", expand=True)
    table.add_column("Coin", width=8)
    table.add_column("Buy Pressure", justify="center", width=22)
    table.add_column("Delta", justify="right", width=12)
    table.add_column("Bias", justify="center", width=10)

    # Get stats by coin
    coin_stats: dict[str, dict] = {}
    for event in aggregator.events:
        if event.value_usd < 1000:
            continue
        if event.coin not in coin_stats:
            coin_stats[event.coin] = {"buy_usd": 0.0, "sell_usd": 0.0}

        if event.side == "buy":
            coin_stats[event.coin]["buy_usd"] += event.value_usd
        else:
            coin_stats[event.coin]["sell_usd"] += event.value_usd

    # Sort by total volume
    sorted_coins = sorted(
        coin_stats.items(),
        key=lambda x: x[1]["buy_usd"] + x[1]["sell_usd"],
        reverse=True,
    )

    for coin, stats in sorted_coins[:10]:
        buy = stats["buy_usd"]
        sell = stats["sell_usd"]
        total = buy + sell
        delta = buy - sell
        buy_pct = (buy / total * 100) if total > 0 else 0

        # Build pressure display with bar
        pressure_text = Text()
        pressure_text.append_text(format_pressure(buy_pct))
        pressure_text.append(" ")
        pressure_text.append_text(build_pressure_bar(buy_pct, width=14))

        table.add_row(
            coin,
            pressure_text,
            format_delta(delta),
            get_bias(delta),
        )

    return table


def build_live_feed(events: list["TradeEvent"]) -> Table:
    """Build the live feed table for large trades."""
    table = Table(title="LIVE FEED", expand=True, show_header=True)
    table.add_column("Time", width=10)
    table.add_column("Exchange", width=8)
    table.add_column("Coin", width=6)
    table.add_column("Side", width=6)
    table.add_column("Value", width=10)
    table.add_column("Price", width=12)

    for event in events:
        side_style = "green" if event.side == "buy" else "red"
        exchange_style = "yellow" if event.exchange == "bybit" else "cyan"
        is_whale = event.value_usd >= 1_000_000
        value_style = "bold yellow" if is_whale else "bold"

        table.add_row(
            format_time(event.timestamp),
            Text(event.exchange.upper(), style=exchange_style),
            event.coin,
            Text(event.side.upper(), style=side_style),
            Text(format_usd(event.value_usd), style=value_style),
            f"${event.price:,.2f}",
        )

    return table


def build_status_bar(
    bybit_connected: bool,
    binance_connected: bool,
    trade_count: int,
) -> Text:
    """Build the status bar."""
    status = Text()

    # Connection status
    status.append(" Connected: ")
    status.append("BYBIT ", style="yellow")
    status.append("● " if bybit_connected else "○ ", style="green" if bybit_connected else "red")
    status.append("BINANCE ", style="cyan")
    status.append("● " if binance_connected else "○ ", style="green" if binance_connected else "red")

    status.append(" │ ", style="dim")
    status.append(f"Trades: {trade_count:,}", style="bold")
    status.append(" │ ", style="dim")
    status.append("Ctrl+C exit", style="dim")

    return status


class OrderFlowDashboard:
    """Dashboard for displaying real-time order flow data."""

    def __init__(self, aggregators: dict[str, "OrderFlowAggregator"]):
        self.aggregators = aggregators
        self.bybit_connected = False
        self.binance_connected = False
        self.console = Console()

    def set_connection_status(self, exchange: str, connected: bool) -> None:
        """Set the connection status for an exchange."""
        if exchange == "bybit":
            self.bybit_connected = connected
        elif exchange == "binance":
            self.binance_connected = connected

    def render(self) -> Group:
        """Render the full dashboard."""
        # Use 15m aggregator for coin table and live feed
        agg_15m = self.aggregators.get("15m")

        return Group(
            "",
            build_timeframe_table(self.aggregators),
            "",
            build_coin_table(agg_15m) if agg_15m else Text("No data"),
            "",
            build_live_feed(agg_15m.recent_feed(12) if agg_15m else []),
            "",
            Panel(
                build_status_bar(
                    self.bybit_connected,
                    self.binance_connected,
                    len(agg_15m.events) if agg_15m else 0,
                ),
                style="dim",
            ),
        )

    def create_live(self) -> Live:
        """Create a Rich Live display."""
        return Live(
            self.render(),
            console=self.console,
            refresh_per_second=2,
            screen=True,
        )
