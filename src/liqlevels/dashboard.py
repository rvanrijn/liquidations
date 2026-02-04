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
        style = "bold red"  # Very close - danger
    elif abs_pct <= 2:
        style = "bold yellow"  # Close
    elif abs_pct <= 5:
        style = "yellow"
    else:
        style = "dim"

    return Text(f"{pct:+.1f}%", style=style)


def format_price(price: float) -> str:
    """Format price based on magnitude."""
    if price >= 10000:
        return f"${price:,.0f}"
    elif price >= 100:
        return f"${price:,.1f}"
    elif price >= 1:
        return f"${price:.2f}"
    else:
        return f"${price:.4f}"


def build_longs_table(data: dict[str, "CoinLiquidations"]) -> Table:
    """Build the longs near liquidation table."""
    table = Table(
        title="🔴 LONGS NEAR LIQUIDATION",
        expand=True,
        title_style="bold red",
    )
    table.add_column("Coin", width=6, style="cyan")
    table.add_column("Leverage", justify="center", width=8)
    table.add_column("Liq Price", justify="right", width=12)
    table.add_column("Est. $", justify="right", width=10)
    table.add_column("At %", justify="right", width=8)

    # Collect all long liquidation levels across coins, sorted by % (closest first)
    all_levels = []
    for coin, coin_data in data.items():
        for level in coin_data.longs_at_risk:
            all_levels.append({
                "coin": coin,
                "leverage": level.leverage,
                "price": level.price,
                "liq_usd": level.estimated_usd,
                "percent": level.percent_from_current,
            })

    # Sort by closest to current price (least negative %)
    all_levels.sort(key=lambda x: x["percent"], reverse=True)

    for level in all_levels[:10]:
        lev_style = "bold red" if level["leverage"] >= 50 else "yellow" if level["leverage"] >= 25 else ""
        table.add_row(
            level["coin"],
            Text(f"{level['leverage']}x", style=lev_style),
            format_price(level["price"]),
            format_usd(level["liq_usd"]),
            format_percent(level["percent"]),
        )

    return table


def build_shorts_table(data: dict[str, "CoinLiquidations"]) -> Table:
    """Build the shorts near liquidation table."""
    table = Table(
        title="🟢 SHORTS NEAR LIQUIDATION",
        expand=True,
        title_style="bold green",
    )
    table.add_column("Coin", width=6, style="cyan")
    table.add_column("Leverage", justify="center", width=8)
    table.add_column("Liq Price", justify="right", width=12)
    table.add_column("Est. $", justify="right", width=10)
    table.add_column("At %", justify="right", width=8)

    # Collect all short liquidation levels across coins
    all_levels = []
    for coin, coin_data in data.items():
        for level in coin_data.shorts_at_risk:
            all_levels.append({
                "coin": coin,
                "leverage": level.leverage,
                "price": level.price,
                "liq_usd": level.estimated_usd,
                "percent": level.percent_from_current,
            })

    # Sort by closest to current price (smallest positive %)
    all_levels.sort(key=lambda x: x["percent"])

    for level in all_levels[:10]:
        lev_style = "bold red" if level["leverage"] >= 50 else "yellow" if level["leverage"] >= 25 else ""
        table.add_row(
            level["coin"],
            Text(f"{level['leverage']}x", style=lev_style),
            format_price(level["price"]),
            format_usd(level["liq_usd"]),
            format_percent(level["percent"]),
        )

    return table


def build_summary_bar(data: dict[str, "CoinLiquidations"]) -> Text:
    """Build summary stats bar."""
    total_oi = sum(c.open_interest_usd for c in data.values())
    # Estimate total at risk (simplified)
    total_long_risk = sum(
        sum(l.estimated_usd for l in c.longs_at_risk)
        for c in data.values()
    )
    total_short_risk = sum(
        sum(l.estimated_usd for l in c.shorts_at_risk)
        for c in data.values()
    )

    bar = Text()
    bar.append(" Total OI: ", style="dim")
    bar.append(format_usd(total_oi), style="bold")
    bar.append("  │  ", style="dim")
    bar.append("Longs at Risk: ", style="dim")
    bar.append(format_usd(total_long_risk), style="bold red")
    bar.append("  │  ", style="dim")
    bar.append("Shorts at Risk: ", style="dim")
    bar.append(format_usd(total_short_risk), style="bold green")

    return bar


def build_coin_summary_table(data: dict[str, "CoinLiquidations"]) -> Table:
    """Build a summary table by coin."""
    table = Table(title="OPEN INTEREST BY COIN", expand=True)
    table.add_column("Coin", width=6, style="cyan")
    table.add_column("Price", justify="right", width=12)
    table.add_column("Open Interest", justify="right", width=12)
    table.add_column("Nearest Long Liq", justify="right", width=14)
    table.add_column("Nearest Short Liq", justify="right", width=14)

    # Sort by OI
    sorted_coins = sorted(data.items(), key=lambda x: x[1].open_interest_usd, reverse=True)

    for coin, coin_data in sorted_coins[:10]:
        # Get nearest liquidation levels
        nearest_long = coin_data.longs_at_risk[0] if coin_data.longs_at_risk else None
        nearest_short = coin_data.shorts_at_risk[0] if coin_data.shorts_at_risk else None

        long_text = Text()
        if nearest_long:
            long_text.append(f"{nearest_long.percent_from_current:+.1f}%", style="red")
            long_text.append(f" ({nearest_long.leverage}x)", style="dim")

        short_text = Text()
        if nearest_short:
            short_text.append(f"{nearest_short.percent_from_current:+.1f}%", style="green")
            short_text.append(f" ({nearest_short.leverage}x)", style="dim")

        table.add_row(
            coin,
            format_price(coin_data.current_price),
            format_usd(coin_data.open_interest_usd),
            long_text,
            short_text,
        )

    return table


def build_status_bar(connected: bool, last_update: str, coin_count: int) -> Text:
    """Build the status bar."""
    status = Text()
    status.append(" Binance: ")
    status.append("● " if connected else "○ ", style="green" if connected else "red")
    status.append(" │ ", style="dim")
    status.append(f"Coins: {coin_count}", style="bold")
    status.append(" │ ", style="dim")
    status.append(f"Updated: {last_update}", style="dim")
    status.append(" │ ", style="dim")
    status.append("Refresh: 10s", style="dim")
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
        self.connected = bool(data)
        from datetime import datetime
        self.last_update = datetime.now().strftime("%H:%M:%S")

    def render(self) -> Group:
        """Render the dashboard."""
        if not self.data:
            return Group(
                "",
                Align.center(Text("Fetching data from Binance...", style="dim")),
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
            build_coin_summary_table(self.data),
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
