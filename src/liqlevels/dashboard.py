# src/liqlevels/dashboard.py
"""Dashboard for liquidation levels visualization."""

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from rich.align import Align

from datetime import datetime
from time import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.liqlevels.client import CoinLiquidations
    from src.liqlevels.models import Battle, LiqSnapshot


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


def build_magnet_status(
    snapshot: "LiqSnapshot | None",
    stats: dict,
) -> Panel:
    """Build current magnet battle status + all-time accuracy."""
    content = Text()

    # Current battle
    if snapshot:
        content.append("ACTIVE BATTLE  ", style="bold yellow")
        content.append(f"BTC {format_price(snapshot.btc_price)}", style="bold")
        content.append("  │  ", style="dim")
        content.append("Long $: ", style="red")
        content.append(format_usd(snapshot.total_long_usd), style="bold red")
        content.append("  Short $: ", style="green")
        content.append(format_usd(snapshot.total_short_usd), style="bold green")
        content.append("  │  ", style="dim")
        content.append("Magnet: ", style="dim")
        magnet = snapshot.bigger_side
        style = "bold red" if magnet == "LONG" else "bold green"
        content.append(f"{magnet} ({snapshot.imbalance_ratio:.1f}x)", style=style)
        content.append("  │  ", style="dim")
        elapsed = time() - snapshot.timestamp
        if elapsed >= 3600:
            content.append(f"{elapsed / 3600:.1f}h", style="dim")
        elif elapsed >= 60:
            content.append(f"{elapsed / 60:.0f}m", style="dim")
        else:
            content.append(f"{elapsed:.0f}s", style="dim")
    else:
        content.append("WAITING  ", style="dim")
        content.append("No active battle (insufficient imbalance)", style="dim")

    content.append("\n")

    # All-time stats
    total = stats.get("total", 0)
    accuracy = stats.get("accuracy", 0.0)
    acc_style = "bold green" if accuracy >= 55 else "bold red" if accuracy < 45 else "bold yellow"
    content.append(f"All-time: {total} battles, ", style="dim")
    content.append(f"{accuracy:.1f}% accurate", style=acc_style)

    return Panel(content, title="MAGNET MONITOR (BTC)", border_style="magenta")


def build_magnet_breakdown(stats: dict, stats_15x: dict, stats_20x: dict) -> Panel:
    """Build accuracy breakdown by imbalance ratio."""
    content = Text()
    for label, s in [("All", stats), (">1.5x", stats_15x), (">2.0x", stats_20x)]:
        n = s.get("total", 0)
        acc = s.get("accuracy", 0.0)
        acc_style = "bold green" if acc >= 55 else "bold red" if acc < 45 else "bold yellow"
        content.append(f" {label}: ", style="dim")
        content.append(f"{acc:.0f}%", style=acc_style)
        content.append(f" ({n})", style="dim")
        content.append("  ", style="dim")
    return Panel(content, title="Accuracy by Imbalance", border_style="dim magenta")


def build_battle_log(recent_battles: list["Battle"]) -> Table:
    """Build a table of the last N resolved battles."""
    table = Table(
        title="RECENT BATTLES",
        expand=True,
        title_style="bold magenta",
    )
    table.add_column("#", width=3, justify="right", style="dim")
    table.add_column("Time", width=8)
    table.add_column("Long $", justify="right", width=9)
    table.add_column("Short $", justify="right", width=9)
    table.add_column("Bigger", justify="center", width=6)
    table.add_column("Hit", justify="center", width=6)
    table.add_column("OK?", justify="center", width=4)
    table.add_column("Ratio", justify="right", width=6)
    table.add_column("Dur", justify="right", width=7)

    for i, b in enumerate(recent_battles, 1):
        ts = datetime.fromtimestamp(b.timestamp).strftime("%H:%M:%S")
        bigger_style = "red" if b.bigger_side == "LONG" else "green"
        hit_style = "red" if b.hit_side == "LONG" else "green"
        ok = Text("Y", style="bold green") if b.hypothesis_correct else Text("N", style="bold red")
        dur_min = b.duration_seconds / 60
        dur_str = f"{dur_min:.0f}m" if dur_min >= 1 else f"{b.duration_seconds:.0f}s"

        table.add_row(
            str(i),
            ts,
            format_usd(b.total_long_usd),
            format_usd(b.total_short_usd),
            Text(b.bigger_side[:1], style=bigger_style),
            Text(b.hit_side[:1], style=hit_style),
            ok,
            f"{b.imbalance_ratio:.2f}",
            dur_str,
        )

    return table


class LiqLevelsDashboard:
    """Dashboard for liquidation levels."""

    def __init__(self):
        self.console = Console()
        self.data: dict[str, "CoinLiquidations"] = {}
        self.connected = False
        self.last_update = "-"
        # Magnet monitor state
        self.monitor_snapshot: "LiqSnapshot | None" = None
        self.monitor_stats: dict = {}
        self.monitor_stats_15x: dict = {}
        self.monitor_stats_20x: dict = {}
        self.monitor_recent: list["Battle"] = []

    def update_data(self, data: dict[str, "CoinLiquidations"]):
        """Update the dashboard data."""
        self.data = data
        self.connected = bool(data)
        self.last_update = datetime.now().strftime("%H:%M:%S")

    def update_monitor_data(
        self,
        snapshot: "LiqSnapshot | None",
        stats: dict,
        stats_15x: dict,
        stats_20x: dict,
        recent_battles: list["Battle"],
    ):
        """Update magnet monitor display data."""
        self.monitor_snapshot = snapshot
        self.monitor_stats = stats
        self.monitor_stats_15x = stats_15x
        self.monitor_stats_20x = stats_20x
        self.monitor_recent = recent_battles

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

        # Build magnet monitor panels
        magnet_status = build_magnet_status(self.monitor_snapshot, self.monitor_stats)
        magnet_breakdown = build_magnet_breakdown(
            self.monitor_stats, self.monitor_stats_15x, self.monitor_stats_20x,
        )
        battle_log = build_battle_log(self.monitor_recent)

        return Group(
            "",
            Align.center(build_summary_bar(self.data)),
            "",
            Columns([longs_table, shorts_table], expand=True, equal=True),
            "",
            magnet_status,
            magnet_breakdown,
            "",
            Columns([build_coin_summary_table(self.data), battle_log], expand=True, equal=True),
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
