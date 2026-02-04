from datetime import datetime
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.align import Align

from src.aggregator import LiquidationAggregator
from src.models import LiquidationEvent


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


def truncate_wallet(wallet: str | None, length: int = 6) -> str:
    """Truncate wallet address for display."""
    if not wallet:
        return "-"
    if len(wallet) <= length + 3:
        return wallet
    return f"{wallet[:length]}..."


def build_ratio_bar(long_pct: float, short_pct: float, width: int = 50) -> Text:
    """Build the long/short ratio bar."""
    long_chars = int(width * long_pct / 100)
    short_chars = width - long_chars

    bar = Text()
    bar.append(" LONGS ", style="bold white on green")
    bar.append(" " + "█" * long_chars, style="green")
    bar.append("░" * short_chars + " ", style="red")
    bar.append(" SHORTS ", style="bold white on red")
    bar.append(f"  ({long_pct:.0f}% / {short_pct:.0f}%)", style="dim")
    return bar


def build_top10_table(events: list[LiquidationEvent]) -> Table:
    """Build the top 10 liquidations table."""
    table = Table(title="TOP 10 LARGEST LIQUIDATIONS (24H)", expand=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Value", style="bold")
    table.add_column("Coin")
    table.add_column("Side")
    table.add_column("Price", justify="right")
    table.add_column("Wallet")
    table.add_column("Time")

    for i, event in enumerate(events, 1):
        side_style = "green" if event.side == "long" else "red"
        value_style = "bold yellow" if event.value_usd > 100_000 else "bold"

        table.add_row(
            str(i),
            Text(format_usd(event.value_usd), style=value_style),
            event.coin,
            Text(event.side.upper(), style=side_style),
            f"${event.price:,.2f}",
            truncate_wallet(event.wallet),
            format_time(event.timestamp),
        )
    return table


def build_coin_table(aggregator: LiquidationAggregator) -> Table:
    """Build the liquidations by coin table."""
    table = Table(title="LIQUIDATIONS BY COIN (24H)", expand=True)
    table.add_column("Coin")
    table.add_column("Count", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Long $", justify="right", style="green")
    table.add_column("Short $", justify="right", style="red")
    table.add_column("Exchange", justify="right")

    by_coin = aggregator.by_coin()

    # Sort by total value descending
    sorted_coins = sorted(by_coin.items(), key=lambda x: x[1]["total_usd"], reverse=True)

    for coin, stats in sorted_coins[:10]:
        # Calculate exchange breakdown for this coin
        coin_events = [e for e in aggregator.events if e.coin == coin]
        bybit_count = sum(1 for e in coin_events if e.exchange == "bybit")
        binance_count = sum(1 for e in coin_events if e.exchange == "binance")
        total_count = bybit_count + binance_count

        if total_count > 0:
            exchange_str = f"BB:{bybit_count * 100 // total_count}% BN:{binance_count * 100 // total_count}%"
        else:
            exchange_str = "-"

        table.add_row(
            coin,
            f"{stats['count']:,}",
            format_usd(stats["total_usd"]),
            format_usd(stats["long_usd"]),
            format_usd(stats["short_usd"]),
            exchange_str,
        )
    return table


def build_live_feed(events: list[LiquidationEvent]) -> Table:
    """Build the live feed table."""
    table = Table(title="LIVE FEED", expand=True, show_header=False)
    table.add_column("Time", width=10)
    table.add_column("Exchange", width=8)
    table.add_column("Coin", width=6)
    table.add_column("Side", width=6)
    table.add_column("Value", width=10)
    table.add_column("Price", width=12)

    for event in events:
        side_style = "green" if event.side == "long" else "red"
        exchange_style = "yellow" if event.exchange == "bybit" else "cyan"

        table.add_row(
            format_time(event.timestamp),
            Text(event.exchange.upper(), style=exchange_style),
            event.coin,
            Text(event.side.upper(), style=side_style),
            format_usd(event.value_usd),
            f"${event.price:,.2f}",
        )
    return table


def build_status_bar(
    bybit_connected: bool,
    binance_connected: bool,
    total_24h: float,
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
    status.append(f"Total 24H: {format_usd(total_24h)}", style="bold")
    status.append(" │ ", style="dim")
    status.append("Ctrl+C exit", style="dim")

    return status


class Dashboard:
    def __init__(self, aggregator: LiquidationAggregator):
        self.aggregator = aggregator
        self.bybit_connected = False
        self.binance_connected = False
        self.console = Console()

    def set_connection_status(self, exchange: str, connected: bool) -> None:
        if exchange == "bybit":
            self.bybit_connected = connected
        elif exchange == "binance":
            self.binance_connected = connected

    def render(self) -> Group:
        """Render the full dashboard."""
        long_pct, short_pct = self.aggregator.long_short_ratio()

        return Group(
            Align.center(build_ratio_bar(long_pct, short_pct)),
            "",
            build_top10_table(self.aggregator.top_10()),
            "",
            build_coin_table(self.aggregator),
            "",
            build_live_feed(self.aggregator.recent_feed(15)),
            "",
            Panel(build_status_bar(
                self.bybit_connected,
                self.binance_connected,
                self.aggregator.total_24h(),
            ), style="dim"),
        )

    def create_live(self) -> Live:
        """Create a Rich Live display."""
        return Live(
            self.render(),
            console=self.console,
            refresh_per_second=2,
            screen=True,
        )
