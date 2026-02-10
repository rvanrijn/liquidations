# src/liqhunt/dashboard.py
"""Rich TUI dashboard for the liquidation hunt signal engine."""

from datetime import datetime

from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.liqhunt.models import Signal
from src.liqlevels.dashboard import (
    build_coin_summary_table,
    build_longs_table,
    build_shorts_table,
    build_magnet_status,
    build_magnet_breakdown,
    build_battle_log,
    build_summary_bar,
    format_price,
    format_usd,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.liqhunt.candles import CandleFetcher
    from src.liqhunt.signal_engine import SignalEngine
    from src.liqlevels.client import CoinLiquidations
    from src.liqlevels.models import Battle, LiqSnapshot


def build_signal_panel(signal: Signal | None, rejection: str) -> Panel:
    """Build the signal status panel."""
    content = Text()

    if signal:
        # Active signal
        dir_style = "bold green" if signal.direction == "LONG" else "bold red"
        content.append(f" {signal.direction}", style=dir_style)
        content.append(f" @ {format_price(signal.entry_price)}", style="bold")
        content.append("  SL ", style="dim")
        content.append(format_price(signal.stop_price), style="bold red")
        content.append("  TP1 ", style="dim")
        content.append(format_price(signal.primary_target), style="bold green")
        content.append("  TP2 ", style="dim")
        content.append(format_price(signal.secondary_target), style="green")
        content.append("\n")
        content.append(f" Risk: {signal.risk_pct * 100:.2f}%", style="yellow")
        content.append(f"  Imbalance: {signal.imbalance_ratio:.2f}x", style="dim")
        content.append(f"  Sweep: {','.join(signal.sweep.criteria_met)}", style="dim")
        content.append("\n")
        content.append(f' "{signal.reasoning}"', style="italic dim")
        border = "green" if signal.direction == "LONG" else "red"
    else:
        content.append(" NO TRADE", style="bold dim")
        if rejection:
            content.append(f" — {rejection}", style="dim")
        border = "dim"

    return Panel(content, title="SIGNAL STATUS", border_style=border)


def build_sweep_monitor(
    candle_fetcher: "CandleFetcher",
    magnet_price: float | None,
) -> Panel:
    """Build the sweep monitor strip showing current candle stats vs thresholds."""
    content = Text()

    latest = candle_fetcher.latest_closed
    avg = candle_fetcher.avg_range

    if latest and avg > 0:
        rng = latest.range
        threshold = avg * 1.2
        rng_style = "bold green" if rng >= threshold else "dim"
        content.append(f" 5m: range {format_price(rng)}", style=rng_style)
        content.append(f" (avg {format_price(avg)}, 1.2x={format_price(threshold)})", style="dim")
        content.append("  |  ", style="dim")
        wick_pct = latest.wick_ratio * 100
        wick_style = "bold green" if wick_pct >= 40 else "dim"
        content.append(f"wick {wick_pct:.0f}%", style=wick_style)

        if magnet_price and magnet_price > 0:
            zone = magnet_price * 0.004
            content.append("  |  ", style="dim")
            content.append(f"zone +/-{format_price(zone)}", style="dim")
    else:
        content.append(" Waiting for candle data...", style="dim")

    return Panel(content, title="SWEEP MONITOR", border_style="dim cyan")


def build_status_bar(connected: bool, last_update: str, coin_count: int) -> Text:
    """Build the status bar."""
    status = Text()
    status.append(" Binance: ")
    status.append("* " if connected else "o ", style="green" if connected else "red")
    status.append(" | ", style="dim")
    status.append(f"Coins: {coin_count}", style="bold")
    status.append(" | ", style="dim")
    status.append(f"Updated: {last_update}", style="dim")
    status.append(" | ", style="dim")
    status.append("Refresh: 10s", style="dim")
    status.append(" | ", style="dim")
    status.append("Ctrl+C exit", style="dim")
    return status


class LiqHuntDashboard:
    """Full dashboard: liqlevels + magnet monitor + signal engine."""

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
        # Signal engine state
        self.signal: Signal | None = None
        self.rejection_reason: str = ""
        self.candle_fetcher: "CandleFetcher | None" = None
        self.magnet_price: float | None = None

    def update_data(self, data: dict[str, "CoinLiquidations"]):
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
        self.monitor_snapshot = snapshot
        self.monitor_stats = stats
        self.monitor_stats_15x = stats_15x
        self.monitor_stats_20x = stats_20x
        self.monitor_recent = recent_battles

    def update_signal_data(
        self,
        signal: Signal | None,
        engine: "SignalEngine",
        candle_fetcher: "CandleFetcher",
        magnet_price: float | None,
    ):
        self.signal = signal
        self.rejection_reason = engine.rejection_reason
        self.candle_fetcher = candle_fetcher
        self.magnet_price = magnet_price

    def render(self) -> Group:
        if not self.data:
            return Group(
                "",
                Align.center(Text("Fetching data from Binance...", style="dim")),
                "",
            )

        longs_table = build_longs_table(self.data)
        shorts_table = build_shorts_table(self.data)
        btc = self.data.get("BTC")
        btc_price = btc.current_price if btc else 0.0
        magnet_status = build_magnet_status(self.monitor_snapshot, self.monitor_stats, btc_price)
        magnet_breakdown = build_magnet_breakdown(
            self.monitor_stats, self.monitor_stats_15x, self.monitor_stats_20x,
        )
        battle_log = build_battle_log(self.monitor_recent)

        signal_panel = build_signal_panel(self.signal, self.rejection_reason)
        sweep_panel = (
            build_sweep_monitor(self.candle_fetcher, self.magnet_price)
            if self.candle_fetcher
            else Panel(Text(" Waiting...", style="dim"), title="SWEEP MONITOR", border_style="dim")
        )

        return Group(
            "",
            Align.center(build_summary_bar(self.data)),
            "",
            Columns([longs_table, shorts_table], expand=True, equal=True),
            "",
            magnet_status,
            magnet_breakdown,
            "",
            signal_panel,
            sweep_panel,
            "",
            Columns([build_coin_summary_table(self.data), battle_log], expand=True, equal=True),
            "",
            Panel(
                build_status_bar(self.connected, self.last_update, len(self.data)),
                style="dim",
            ),
        )

    def create_live(self) -> Live:
        return Live(
            self.render(),
            console=self.console,
            refresh_per_second=1,
            screen=True,
        )
