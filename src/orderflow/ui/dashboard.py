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
    from src.orderflow.models import OrderFlowEvent


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


def build_stats_bar(aggregator: "OrderFlowAggregator", label: str = "15M") -> Text:
    """Build the stats bar showing totals."""
    buy, sell, delta = aggregator.totals()
    total = buy + sell

    bar = Text()
    bar.append(f" [{label}] ", style="bold cyan")
    bar.append("Total: ", style="dim")
    bar.append(format_usd(total), style="bold")
    bar.append(" │ ", style="dim")
    bar.append("Buy: ", style="dim")
    bar.append(format_usd(buy), style="green")
    bar.append(" │ ", style="dim")
    bar.append("Sell: ", style="dim")
    bar.append(format_usd(sell), style="red")
    bar.append(" │ ", style="dim")
    bar.append("Delta: ", style="dim")
    bar.append_text(format_delta(delta))

    return bar


def build_exchange_table(aggregator: "OrderFlowAggregator", label: str = "15M") -> Table:
    """Build the order flow by exchange table."""
    table = Table(title=f"ORDER FLOW BY EXCHANGE ({label})", expand=True)
    table.add_column("Exchange", width=10)
    table.add_column("Buy $", justify="right", style="green")
    table.add_column("Sell $", justify="right", style="red")
    table.add_column("Delta", justify="right")
    table.add_column("Flow", justify="center", width=20)

    by_exchange = aggregator.by_exchange()

    for exchange in ["bybit", "binance"]:
        stats = by_exchange.get(exchange, {"buy_usd": 0.0, "sell_usd": 0.0})
        buy = stats["buy_usd"]
        sell = stats["sell_usd"]
        delta = buy - sell
        total = buy + sell

        exchange_style = "yellow" if exchange == "bybit" else "cyan"

        table.add_row(
            Text(exchange.upper(), style=exchange_style),
            format_usd(buy),
            format_usd(sell),
            format_delta(delta),
            build_flow_bar(buy, sell, width=16),
        )

    return table


def build_flow_bar(buy: float, sell: float, width: int = 12) -> Text:
    """Build a mini buy/sell flow bar."""
    total = buy + sell
    if total == 0:
        return Text("─" * width, style="dim")

    buy_chars = int(width * buy / total)
    sell_chars = width - buy_chars

    bar = Text()
    bar.append("█" * buy_chars, style="green")
    bar.append("█" * sell_chars, style="red")
    return bar


def build_size_table(aggregator: "OrderFlowAggregator", label: str = "15M") -> Table:
    """Build the order flow by size table."""
    table = Table(title=f"ORDER FLOW BY SIZE ({label})", expand=True)
    table.add_column("Category", width=10)
    table.add_column("Buy $", justify="right", style="green")
    table.add_column("Sell $", justify="right", style="red")
    table.add_column("Delta", justify="right")
    table.add_column("Flow", justify="center", width=20)

    by_size = aggregator.by_size()
    categories = ["whale", "large", "medium", "small"]

    for category in categories:
        stats = by_size.get(category, {"buy_usd": 0.0, "sell_usd": 0.0})
        buy = stats["buy_usd"]
        sell = stats["sell_usd"]
        delta = buy - sell

        is_whale = category == "whale"
        cat_style = "bold yellow" if is_whale else "white"

        table.add_row(
            Text(category.upper(), style=cat_style),
            Text(format_usd(buy), style="bold yellow" if is_whale else "green"),
            Text(format_usd(sell), style="bold yellow" if is_whale else "red"),
            format_delta(delta),
            build_flow_bar(buy, sell, width=16),
        )

    return table


def build_pressure_bar(buy_pct: float, sell_pct: float, width: int = 50) -> Text:
    """Build the buy/sell pressure bar."""
    buy_chars = int(width * buy_pct / 100)
    sell_chars = width - buy_chars

    bar = Text()
    bar.append(" BUY ", style="bold white on green")
    bar.append(" " + "█" * buy_chars, style="green")
    bar.append("░" * sell_chars + " ", style="red")
    bar.append(" SELL ", style="bold white on red")
    bar.append(f"  ({buy_pct:.0f}% / {sell_pct:.0f}%)", style="dim")
    return bar


def build_live_feed(events: list["OrderFlowEvent"]) -> Table:
    """Build the live feed table for large trades."""
    table = Table(title="LIVE FEED (Large Trades)", expand=True, show_header=True)
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

    def __init__(
        self,
        aggregator_15m: "OrderFlowAggregator",
        aggregator_1h: "OrderFlowAggregator | None" = None,
    ):
        self.aggregator_15m = aggregator_15m
        self.aggregator_1h = aggregator_1h
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
        buy_pct_15m, sell_pct_15m = self.aggregator_15m.buy_sell_pressure()

        elements = [
            Align.center(build_stats_bar(self.aggregator_15m, "15M")),
        ]

        # Add 1h stats if available
        if self.aggregator_1h:
            elements.append(Align.center(build_stats_bar(self.aggregator_1h, "1H")))

        elements.extend([
            "",
            Align.center(build_pressure_bar(buy_pct_15m, sell_pct_15m)),
            "",
            build_exchange_table(self.aggregator_15m, "15M"),
        ])

        # Add 1h exchange table if available
        if self.aggregator_1h:
            elements.append(build_exchange_table(self.aggregator_1h, "1H"))

        elements.extend([
            "",
            build_size_table(self.aggregator_15m, "15M"),
        ])

        # Add 1h size table if available
        if self.aggregator_1h:
            elements.append(build_size_table(self.aggregator_1h, "1H"))

        elements.extend([
            "",
            build_live_feed(self.aggregator_15m.recent_feed(10)),
            "",
            Panel(
                build_status_bar(
                    self.bybit_connected,
                    self.binance_connected,
                    len(self.aggregator_15m.events),
                ),
                style="dim",
            ),
        ])

        return Group(*elements)

    def create_live(self) -> Live:
        """Create a Rich Live display."""
        return Live(
            self.render(),
            console=self.console,
            refresh_per_second=2,
            screen=True,
        )
