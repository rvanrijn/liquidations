# src/liqlevels/dashboard.py
"""Dashboard for liquidation levels visualization."""

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from rich.align import Align

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.liqlevels.client import CoinLiquidations


def format_usd(value: float) -> str:
    """Format USD value with K/M/B suffix."""
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    elif value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.0f}"


def format_percent(pct: float) -> Text:
    """Format percentage with color based on proximity."""
    abs_pct = abs(pct)
    if abs_pct <= 1:
        style = "bold red"  # Very close
    elif abs_pct <= 3:
        style = "bold yellow"  # Close
    elif abs_pct <= 5:
        style = "yellow"
    else:
        style = "dim"

    sign = "+" if pct > 0 else ""
    return Text(f"{sign}{pct:.1f}%", style=style)


def format_price(price: float) -> str:
    """Format price based on magnitude."""
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    else:
        return f"${price:.4f}"


def build_longs_table(data: dict[str, "CoinLiquidations"]) -> Table:
    """Build the longs near liquidation table."""
    table = Table(title="🔴 LONGS NEAR LIQUIDATION", expand=True, title_style="bold red")
    table.add_column("Coin", width=6, style="cyan")
    table.add_column("Price", justify="right", width=12)
    table.add_column("Liq $", justify="right", width=10)
    table.add_column("At %", justify="right", width=8)

    # Collect all long liquidation levels across coins
    all_levels = []
    for coin, coin_data in data.items():
        for level in coin_data.longs_at_risk:
            all_levels.append({
                "coin": coin,
                "price": level.price,
                "liq_usd": level.long_liq_usd,
                "percent": level.percent_from_current,
            })

    # Sort by closest to liquidation (smallest negative %)
    all_levels.sort(key=lambda x: x["percent"], reverse=True)

    for level in all_levels[:10]:
        table.add_row(
            level["coin"],
            format_price(level["price"]),
            format_usd(level["liq_usd"]),
            format_percent(level["percent"]),
        )

    return table


def build_shorts_table(data: dict[str, "CoinLiquidations"]) -> Table:
    """Build the shorts near liquidation table."""
    table = Table(title="🟢 SHORTS NEAR LIQUIDATION", expand=True, title_style="bold green")
    table.add_column("Coin", width=6, style="cyan")
    table.add_column("Price", justify="right", width=12)
    table.add_column("Liq $", justify="right", width=10)
    table.add_column("At %", justify="right", width=8)

    # Collect all short liquidation levels across coins
    all_levels = []
    for coin, coin_data in data.items():
        for level in coin_data.shorts_at_risk:
            all_levels.append({
                "coin": coin,
                "price": level.price,
                "liq_usd": level.short_liq_usd,
                "percent": level.percent_from_current,
            })

    # Sort by closest to liquidation (smallest positive %)
    all_levels.sort(key=lambda x: x["percent"])

    for level in all_levels[:10]:
        table.add_row(
            level["coin"],
            format_price(level["price"]),
            format_usd(level["liq_usd"]),
            format_percent(level["percent"]),
        )

    return table


def build_summary_bar(data: dict[str, "CoinLiquidations"]) -> Text:
    """Build summary stats bar."""
    total_long = sum(c.total_long_liq_usd for c in data.values())
    total_short = sum(c.total_short_liq_usd for c in data.values())

    bar = Text()
    bar.append(" Total Longs at Risk: ", style="dim")
    bar.append(format_usd(total_long), style="bold red")
    bar.append("  │  ", style="dim")
    bar.append("Total Shorts at Risk: ", style="dim")
    bar.append(format_usd(total_short), style="bold green")

    return bar


def build_status_bar(connected: bool, last_update: str, coin_count: int) -> Text:
    """Build the status bar."""
    status = Text()
    status.append(" Coinglass: ")
    status.append("● " if connected else "○ ", style="green" if connected else "red")
    status.append(" │ ", style="dim")
    status.append(f"Coins: {coin_count}", style="bold")
    status.append(" │ ", style="dim")
    status.append(f"Updated: {last_update}", style="dim")
    status.append(" │ ", style="dim")
    status.append("Ctrl+C exit", style="dim")

    return status


class LiqLevelsDashboard:
    """Dashboard for liquidation levels."""

    def __init__(self):
        self.console = Console()
        self.data: dict[str, "CoinLiquidations"] = {}
        self.connected = False
        self.last_update = "-"

    def update_data(self, data: dict[str, "CoinLiquidations"]):
        """Update the dashboard data."""
        self.data = data
        self.connected = True
        from datetime import datetime
        self.last_update = datetime.now().strftime("%H:%M:%S")

    def render(self) -> Group:
        """Render the dashboard."""
        if not self.data:
            return Group(
                "",
                Align.center(Text("Fetching liquidation data...", style="dim")),
                "",
            )

        longs_table = build_longs_table(self.data)
        shorts_table = build_shorts_table(self.data)

        return Group(
            "",
            Align.center(build_summary_bar(self.data)),
            "",
            Columns([longs_table, shorts_table], expand=True, equal=True),
            "",
            Panel(
                build_status_bar(
                    self.connected,
                    self.last_update,
                    len(self.data),
                ),
                style="dim",
            ),
        )

    def create_live(self) -> Live:
        """Create a Rich Live display."""
        return Live(
            self.render(),
            console=self.console,
            refresh_per_second=1,
            screen=True,
        )
