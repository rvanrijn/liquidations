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
    from src.liqhunt.battle_trader import BattleTrader
    from src.liqhunt.candles import CandleFetcher
    from src.liqhunt.paper_trader import PaperTrader
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
        # Confidence badge
        conf = signal.confidence
        if conf >= 80:
            conf_style = "bold green"
        elif conf >= 50:
            conf_style = "yellow"
        else:
            conf_style = "dim"
        content.append(f"  Confidence: {conf}", style=conf_style)
        # Quality gate info
        if signal.quality_score > 0 or signal.gate_probability > 0:
            content.append("\n")
            content.append(f" Q={signal.quality_score:.2f}", style="bold cyan")
            content.append(f" p={signal.gate_probability:.2f}", style="bold cyan")
            content.append(f" size={signal.notional_scale:.1f}x", style="bold cyan")
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
    btc_delta_1m: float = 0.0,
    price_range_6h: float = 0.0,
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
            zone = magnet_price * 0.01  # matches ZONE_PCT in sweep.py
            content.append("  |  ", style="dim")
            content.append(f"zone +/-{format_price(zone)}", style="dim")
    else:
        content.append(" Waiting for candle data...", style="dim")

    # Orderflow delta
    content.append("  |  ", style="dim")
    if btc_delta_1m != 0:
        delta_m = btc_delta_1m / 1e6
        delta_style = "bold green" if btc_delta_1m > 0 else "bold red"
        content.append(f"1m delta {delta_m:+.1f}M", style=delta_style)
    else:
        content.append("1m delta --", style="dim")

    # 6h volatility range
    content.append("  |  ", style="dim")
    from src.liqhunt.signal_engine import VOL_RANGE_MIN
    vol_ok = price_range_6h >= VOL_RANGE_MIN
    vol_style = "bold green" if vol_ok else "bold red"
    content.append(f"6h range ${price_range_6h:,.0f}", style=vol_style)
    if not vol_ok:
        content.append(f" (<${VOL_RANGE_MIN:,})", style="dim red")

    return Panel(content, title="SWEEP MONITOR", border_style="dim cyan")


def build_paper_trader_panel(paper_trader: "PaperTrader", btc_price: float = 0.0) -> Panel:
    """Build the paper trader status panel."""
    from datetime import datetime
    from src.liqhunt.paper_trader import STARTING_BALANCE

    content = Text()
    pos = paper_trader.position
    stats = paper_trader.stats
    balance = paper_trader.balance
    pnl_total = balance - STARTING_BALANCE
    pnl_style = "bold green" if pnl_total >= 0 else "bold red"

    # Current position
    if pos:
        dir_style = "bold green" if pos.direction == "LONG" else "bold red"
        content.append(f" {pos.direction}", style=dir_style)
        content.append(f" @ {format_price(pos.entry_price)}", style="bold")
        content.append(f"  notional {format_usd(pos.notional)}", style="dim")
        elapsed = (datetime.now().timestamp() - pos.entry_time)
        if elapsed >= 3600:
            content.append(f"  ({elapsed / 3600:.1f}h)", style="dim")
        elif elapsed >= 60:
            content.append(f"  ({elapsed / 60:.0f}m)", style="dim")
        else:
            content.append(f"  ({elapsed:.0f}s)", style="dim")

        # Targets line — real battle resolve prices (snapshot ± 0.5%)
        content.append("\n")
        if pos.stop_price > 0:
            content.append(f" SL ", style="dim")
            content.append(format_price(pos.stop_price), style="bold red")
        if pos.snapshot_price > 0:
            from src.liqlevels.models import RESOLVE_MOVE_PCT
            resolve_up = pos.snapshot_price * (1 + RESOLVE_MOVE_PCT / 100)
            resolve_down = pos.snapshot_price * (1 - RESOLVE_MOVE_PCT / 100)
            if pos.direction == "SHORT":
                win_price, loss_price = resolve_down, resolve_up
            else:
                win_price, loss_price = resolve_up, resolve_down
            content.append(f"  WIN ", style="dim")
            content.append(format_price(win_price), style="bold green")
            content.append(f"  LOSS ", style="dim")
            content.append(format_price(loss_price), style="bold red")

        # Live unrealized P&L
        if btc_price > 0:
            if pos.direction == "LONG":
                upnl_pct = (btc_price - pos.entry_price) / pos.entry_price * 100
            else:
                upnl_pct = (pos.entry_price - btc_price) / pos.entry_price * 100
            upnl_usd = upnl_pct / 100 * pos.notional
            upnl_style = "bold green" if upnl_usd >= 0 else "bold red"
            content.append(f"  │  ", style="dim")
            content.append(f"uP&L ${upnl_usd:+,.0f}", style=upnl_style)
            content.append(f" ({upnl_pct:+.2f}%)", style=upnl_style)
    else:
        content.append(" FLAT", style="dim")
        content.append(" — waiting for Liquidation Storm signal", style="dim")

    # Balance line
    content.append("\n")
    content.append(f" Balance: ", style="dim")
    content.append(f"${balance:,.2f}", style="bold")
    content.append(f"  ({pnl_total:+,.2f})", style=pnl_style)
    content.append(f"  │  ", style="dim")
    content.append(f"Trades: {stats.get('total', 0)}", style="bold")
    if stats.get("total", 0) > 0:
        wr = stats.get("win_rate", 0)
        wr_style = "bold green" if wr >= 80 else "bold yellow" if wr >= 60 else "bold red"
        content.append(f"  WR: ", style="dim")
        content.append(f"{wr:.0f}%", style=wr_style)
        content.append(f"  Avg: ", style="dim")
        avg = stats.get("avg_pnl", 0)
        content.append(f"${avg:+,.2f}", style="green" if avg >= 0 else "red")

    # Recent trades
    if paper_trader.recent_trades:
        content.append("\n")
        for t in paper_trader.recent_trades[:3]:
            ts = datetime.fromtimestamp(t.exit_time).strftime("%H:%M")
            pnl_s = "green" if t.pnl_usd >= 0 else "red"
            content.append(f" {ts} ", style="dim")
            content.append(f"{t.direction[0]}", style="green" if t.direction == "LONG" else "red")
            content.append(f" {t.move_pct:+.2f}%", style=pnl_s)
            content.append(f" ${t.pnl_usd:+,.0f}", style=pnl_s)
            content.append(f"  ", style="dim")

    border = "green" if pnl_total >= 0 else "red" if pnl_total < -100 else "yellow"
    return Panel(content, title="PAPER TRADER (Liquidation Storm)", border_style=border)


def build_battle_trader_panel(battle_trader: "BattleTrader", btc_price: float = 0.0) -> Panel:
    """Build the battle trader status panel (OI gate only)."""
    from datetime import datetime
    from src.liqhunt.battle_trader import STARTING_BALANCE

    content = Text()
    pos = battle_trader.position
    stats = battle_trader.stats
    balance = battle_trader.balance
    pnl_total = balance - STARTING_BALANCE
    pnl_style = "bold green" if pnl_total >= 0 else "bold red"

    if pos:
        dir_style = "bold green" if pos.direction == "LONG" else "bold red"
        content.append(f" {pos.direction}", style=dir_style)
        content.append(f" @ {format_price(pos.entry_price)}", style="bold")
        content.append(f"  notional {format_usd(pos.notional)}", style="dim")
        elapsed = (datetime.now().timestamp() - pos.entry_time)
        if elapsed >= 3600:
            content.append(f"  ({elapsed / 3600:.1f}h)", style="dim")
        elif elapsed >= 60:
            content.append(f"  ({elapsed / 60:.0f}m)", style="dim")
        else:
            content.append(f"  ({elapsed:.0f}s)", style="dim")

        content.append("\n")
        if pos.stop_price > 0:
            content.append(f" SL ", style="dim")
            content.append(format_price(pos.stop_price), style="bold red")
        if pos.snapshot_price > 0:
            from src.liqlevels.models import RESOLVE_MOVE_PCT
            resolve_up = pos.snapshot_price * (1 + RESOLVE_MOVE_PCT / 100)
            resolve_down = pos.snapshot_price * (1 - RESOLVE_MOVE_PCT / 100)
            if pos.direction == "SHORT":
                tp_price, sl_price = resolve_down, resolve_up
            else:
                tp_price, sl_price = resolve_up, resolve_down
            tp_pnl = RESOLVE_MOVE_PCT / 100 * pos.notional
            content.append(f"  TP ", style="dim")
            content.append(f"{format_price(tp_price)} (+${tp_pnl:,.0f})", style="bold green")
            content.append(f"  SL ", style="dim")
            content.append(f"{format_price(sl_price)} (-${tp_pnl:,.0f})", style="bold red")

        if btc_price > 0:
            if pos.direction == "LONG":
                upnl_pct = (btc_price - pos.entry_price) / pos.entry_price * 100
            else:
                upnl_pct = (pos.entry_price - btc_price) / pos.entry_price * 100
            upnl_usd = upnl_pct / 100 * pos.notional
            upnl_style = "bold green" if upnl_usd >= 0 else "bold red"
            content.append(f"  │  ", style="dim")
            content.append(f"uP&L ${upnl_usd:+,.0f}", style=upnl_style)
            content.append(f" ({upnl_pct:+.2f}%)", style=upnl_style)
    else:
        content.append(" FLAT", style="dim")
        content.append(" — waiting for OI gate", style="dim")

    content.append("\n")
    content.append(f" Balance: ", style="dim")
    content.append(f"${balance:,.2f}", style="bold")
    content.append(f"  ({pnl_total:+,.2f})", style=pnl_style)
    content.append(f"  │  ", style="dim")
    content.append(f"Trades: {stats.get('total', 0)}", style="bold")
    if stats.get("total", 0) > 0:
        wr = stats.get("win_rate", 0)
        wr_style = "bold green" if wr >= 80 else "bold yellow" if wr >= 60 else "bold red"
        content.append(f"  WR: ", style="dim")
        content.append(f"{wr:.0f}%", style=wr_style)
        content.append(f"  Avg: ", style="dim")
        avg = stats.get("avg_pnl", 0)
        content.append(f"${avg:+,.2f}", style="green" if avg >= 0 else "red")

    from src.liqhunt.battle_trader import COOLDOWN_SEC
    if battle_trader.last_trade_time > 0:
        cd_left = COOLDOWN_SEC - (datetime.now().timestamp() - battle_trader.last_trade_time)
        if cd_left > 0:
            content.append("\n")
            content.append(f" CD {cd_left:.0f}s", style="bold yellow")

    if battle_trader.recent_trades:
        content.append("\n")
        for t in battle_trader.recent_trades[:3]:
            ts = datetime.fromtimestamp(t.exit_time).strftime("%H:%M")
            pnl_s = "green" if t.pnl_usd >= 0 else "red"
            content.append(f" {ts} ", style="dim")
            content.append(f"{t.direction[0]}", style="green" if t.direction == "LONG" else "red")
            content.append(f" {t.move_pct:+.2f}%", style=pnl_s)
            content.append(f" ${t.pnl_usd:+,.0f}", style=pnl_s)
            content.append(f"  ", style="dim")

    border = "green" if pnl_total >= 0 else "red" if pnl_total < -100 else "yellow"
    return Panel(content, title="BATTLE TRADER (OI gate only)", border_style=border)


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
        self.btc_delta_1m: float = 0.0
        self.price_range_6h: float = 0.0
        self.paper_trader: "PaperTrader | None" = None
        self.battle_trader: "BattleTrader | None" = None

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
        btc_delta_1m: float = 0.0,
        price_range_6h: float = 0.0,
        paper_trader: "PaperTrader | None" = None,
        battle_trader: "BattleTrader | None" = None,
    ):
        self.signal = signal
        self.rejection_reason = engine.rejection_reason
        self.candle_fetcher = candle_fetcher
        self.magnet_price = magnet_price
        self.btc_delta_1m = btc_delta_1m
        self.price_range_6h = price_range_6h
        self.paper_trader = paper_trader
        self.battle_trader = battle_trader

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
        btc_oi = btc.open_interest_usd if btc else 0.0
        btc_funding = btc.funding_rate if btc else 0.0
        magnet_status = build_magnet_status(self.monitor_snapshot, self.monitor_stats, btc_price, btc_oi, btc_funding)
        magnet_breakdown = build_magnet_breakdown(
            self.monitor_stats, self.monitor_stats_15x, self.monitor_stats_20x,
        )
        battle_log = build_battle_log(self.monitor_recent)

        signal_panel = build_signal_panel(self.signal, self.rejection_reason)
        sweep_panel = (
            build_sweep_monitor(self.candle_fetcher, self.magnet_price, self.btc_delta_1m, self.price_range_6h)
            if self.candle_fetcher
            else Panel(Text(" Waiting...", style="dim"), title="SWEEP MONITOR", border_style="dim")
        )

        paper_panel = (
            build_paper_trader_panel(self.paper_trader, btc_price)
            if self.paper_trader
            else Panel(Text(" Waiting...", style="dim"), title="PAPER TRADER", border_style="dim")
        )

        battle_panel = (
            build_battle_trader_panel(self.battle_trader, btc_price)
            if self.battle_trader
            else Panel(Text(" Waiting...", style="dim"), title="BATTLE TRADER", border_style="dim")
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
            paper_panel,
            battle_panel,
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
