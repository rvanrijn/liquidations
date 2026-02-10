from datetime import datetime
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.align import Align

from src.scalper.models import BotState, Bias, Position, TradeRecord


def format_usd(value: float) -> str:
    """Format USD value with K/M suffix."""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.0f}"


def format_price(price: float) -> str:
    """Format BTC price with proper formatting."""
    return f"${price:,.2f}"


def format_pnl(pnl: float) -> Text:
    """Format P&L with color (green if positive, red if negative)."""
    if pnl >= 0:
        return Text(f"+{format_usd(pnl)}", style="bold green")
    else:
        return Text(f"-{format_usd(abs(pnl))}", style="bold red")


def format_percent(pct: float) -> Text:
    """Format percentage with color."""
    if pct >= 0:
        return Text(f"+{pct:.2f}%", style="green")
    else:
        return Text(f"{pct:.2f}%", style="red")


def format_time(timestamp_ms: int) -> str:
    """Format timestamp to HH:MM:SS."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    return dt.strftime("%H:%M:%S")


def build_header_bar(state: BotState, current_price: float, unrealized_pnl: float) -> Text:
    """Build the header bar with key stats."""
    header = Text()

    # Balance
    header.append(" Balance: ", style="dim")
    header.append(format_usd(state.account_balance), style="bold cyan")

    # Daily P&L
    header.append("  │  ", style="dim")
    header.append("Daily P&L: ", style="dim")
    header.append_text(format_pnl(state.daily_pnl))

    # Unrealized P&L
    header.append("  │  ", style="dim")
    header.append("Unrealized: ", style="dim")
    header.append_text(format_pnl(unrealized_pnl))

    # BTC Price
    header.append("  │  ", style="dim")
    header.append("BTC: ", style="dim")
    header.append(format_price(current_price), style="bold yellow")

    # Status
    header.append("  │  ", style="dim")
    if state.is_active:
        header.append("ACTIVE", style="bold green")
    else:
        header.append("STOPPED", style="bold red")

    return header


def build_conditions_panel(
    bias: Bias | None,
    delta_1m: float,
    delta_5m: float,
    buy_pressure: float,
    vwap: float,
    high_5m: float,
    low_5m: float,
) -> Table:
    """Build the market conditions table."""
    table = Table(title="MARKET CONDITIONS", expand=True, show_header=True)
    table.add_column("Metric", style="dim", width=15)
    table.add_column("Value", justify="right")

    # Bias
    if bias:
        bias_text = Text(bias.direction, style={
            "LONG": "bold green",
            "SHORT": "bold red",
            "NONE": "dim",
        }.get(bias.direction, "dim"))
        table.add_row("Bias", bias_text)

        # Delta 15m
        delta_15m_text = format_pnl(bias.delta_15m) if bias.delta_15m else Text("$0", style="dim")
        table.add_row("Delta 15m", delta_15m_text)

        # Delta 1m
        delta_1m_text = format_pnl(delta_1m) if delta_1m else Text("$0", style="dim")
        table.add_row("Delta 1m", delta_1m_text)

        # Delta 5m
        delta_5m_text = format_pnl(delta_5m) if delta_5m else Text("$0", style="dim")
        table.add_row("Delta 5m", delta_5m_text)

        # Buy Pressure
        pressure_color = "green" if buy_pressure >= 60 else "red" if buy_pressure <= 40 else "yellow"
        table.add_row("Buy Pressure", Text(f"{buy_pressure:.1f}%", style=pressure_color))

        # VWAP
        table.add_row("VWAP", Text(format_price(vwap), style="cyan"))

        # 5m High/Low
        table.add_row("5m High", Text(format_price(high_5m), style="dim"))
        table.add_row("5m Low", Text(format_price(low_5m), style="dim"))

        # Liquidation levels
        table.add_row("Long Liq $", Text(format_usd(bias.long_liq_usd), style="green"))
        table.add_row("Short Liq $", Text(format_usd(bias.short_liq_usd), style="red"))

        # Ratio
        total = bias.long_liq_usd + bias.short_liq_usd
        if total > 0:
            ratio = bias.long_liq_usd / total * 100
            ratio_text = Text(f"{ratio:.0f}% / {100-ratio:.0f}%", style="dim")
        else:
            ratio_text = Text("N/A", style="dim")
        table.add_row("Ratio L/S", ratio_text)
    else:
        table.add_row("Status", Text("Waiting for data...", style="dim"))

    return table


def build_entry_checklist(
    short_conds: list[tuple[str, bool, str]],
    long_conds: list[tuple[str, bool, str]],
) -> Columns:
    """Build side-by-side entry condition checklists."""
    # SHORT table
    short_table = Table(title="SHORT CONDITIONS", expand=True, show_header=True)
    short_table.add_column("", width=2)
    short_table.add_column("Condition", style="dim")
    short_table.add_column("Value", justify="right")

    short_met = 0
    for label, met, value in short_conds:
        icon = Text("V ", style="bold green") if met else Text("X ", style="bold red")
        val_style = "green" if met else "red"
        short_table.add_row(icon, label, Text(value, style=val_style))
        if met:
            short_met += 1

    short_table.caption = f"{short_met}/{len(short_conds)} conditions met"

    # LONG table
    long_table = Table(title="LONG CONDITIONS", expand=True, show_header=True)
    long_table.add_column("", width=2)
    long_table.add_column("Condition", style="dim")
    long_table.add_column("Value", justify="right")

    long_met = 0
    for label, met, value in long_conds:
        icon = Text("V ", style="bold green") if met else Text("X ", style="bold red")
        val_style = "green" if met else "red"
        long_table.add_row(icon, label, Text(value, style=val_style))
        if met:
            long_met += 1

    long_table.caption = f"{long_met}/{len(long_conds)} conditions met"

    return Columns([short_table, long_table], expand=True, equal=True)


def build_position_panel(position: Position | None, current_price: float) -> Panel:
    """Build the position panel."""
    if not position:
        content = Text("No open position - waiting for signal", style="dim italic", justify="center")
        return Panel(content, title="POSITION", style="dim")

    # Calculate unrealized P&L
    if position.side == "LONG":
        pnl_usd = (current_price - position.entry_price) / position.entry_price * position.size_usd * position.leverage * position.remaining_pct
    else:
        pnl_usd = (position.entry_price - current_price) / position.entry_price * position.size_usd * position.leverage * position.remaining_pct

    pnl_pct = (pnl_usd / (position.size_usd * position.remaining_pct)) * 100

    # Build content
    content = Table.grid(padding=(0, 2))
    content.add_column(style="dim")
    content.add_column()
    content.add_column(style="dim")
    content.add_column()

    # Row 1: Side, Entry
    side_style = "bold green" if position.side == "LONG" else "bold red"
    content.add_row(
        "Side:", Text(position.side, style=side_style),
        "Entry:", Text(format_price(position.entry_price), style="bold"),
    )

    # Row 2: Size, Leverage
    content.add_row(
        "Size:", format_usd(position.size_usd * position.remaining_pct),
        "Leverage:", Text(f"{position.leverage}x", style="cyan"),
    )

    # Row 3: P&L, %
    content.add_row(
        "P&L:", format_pnl(pnl_usd),
        "P&L %:", format_percent(pnl_pct),
    )

    # Row 4: Stop, TP1
    content.add_row(
        "Stop:", Text(format_price(position.stop_loss), style="red"),
        "TP1:", Text(format_price(position.tp1), style="green" if not position.tp1_hit else "dim"),
    )

    # Row 5: Current, TP2
    content.add_row(
        "Current:", Text(format_price(current_price), style="yellow"),
        "TP2:", Text(format_price(position.tp2), style="green"),
    )

    # TP1 status
    if position.tp1_hit:
        content.add_row(
            "",
            Text(f"TP1 HIT - {position.remaining_pct * 100:.0f}% remaining", style="dim italic"),
        )

    panel_style = "green" if position.side == "LONG" else "red"
    return Panel(content, title="POSITION", style=panel_style)


def build_trade_log(trades: list[TradeRecord]) -> Table:
    """Build the trade log table."""
    table = Table(title="TODAY'S TRADES", expand=True)
    table.add_column("#", width=3, style="dim")
    table.add_column("Time", width=10)
    table.add_column("Side", width=6)
    table.add_column("Entry", justify="right", width=10)
    table.add_column("Exit", justify="right", width=10)
    table.add_column("P&L", justify="right", width=10)
    table.add_column("R", justify="right", width=6)
    table.add_column("Reason", width=12)

    # Show last 6 trades
    for i, trade in enumerate(trades[-6:], 1):
        side_style = "green" if trade.side == "LONG" else "red"

        table.add_row(
            str(i),
            format_time(trade.exit_time),
            Text(trade.side, style=side_style),
            format_price(trade.entry_price),
            format_price(trade.exit_price),
            format_pnl(trade.pnl_usd),
            Text(f"{trade.r_multiple:+.1f}R", style="green" if trade.r_multiple > 0 else "red"),
            Text(trade.exit_reason, style="dim"),
        )

    return table


def build_stats_bar(state: BotState, hl_connected: bool, binance_connected: bool, bybit_connected: bool) -> Text:
    """Build the stats/status bar."""
    stats = Text()

    # Trades today
    stats.append(" Trades: ", style="dim")
    stats.append(f"{state.daily_trades}/{state.max_daily_trades}", style="bold")

    # Win/Loss
    stats.append("  │  ", style="dim")
    stats.append("W/L: ", style="dim")
    stats.append(f"{state.wins}", style="green")
    stats.append("/", style="dim")
    stats.append(f"{state.losses}", style="red")

    # Win Rate
    stats.append("  │  ", style="dim")
    stats.append("Win Rate: ", style="dim")
    stats.append(f"{state.win_rate:.0f}%", style="bold cyan")

    # Connections
    stats.append("  │  ", style="dim")
    stats.append("Connected: ")
    stats.append("HL ", style="magenta")
    stats.append("● " if hl_connected else "○ ", style="green" if hl_connected else "red")
    stats.append("BINANCE ", style="cyan")
    stats.append("● " if binance_connected else "○ ", style="green" if binance_connected else "red")
    stats.append("BYBIT ", style="yellow")
    stats.append("● " if bybit_connected else "○ ", style="green" if bybit_connected else "red")

    stats.append("  │  ", style="dim")
    stats.append("Ctrl+C exit", style="dim")

    return stats


class ScalperDashboard:
    """Rich TUI dashboard for the BTC scalping bot."""

    def __init__(self, state: BotState):
        self.state = state
        self.current_price = 0.0
        self.unrealized_pnl = 0.0
        self.delta_1m = 0.0
        self.delta_5m = 0.0
        self.buy_pressure = 0.0
        self.vwap = 0.0
        self.high_5m = 0.0
        self.low_5m = 0.0
        self.today_trades: list[TradeRecord] = []
        self.short_conds: list[tuple[str, bool, str]] = []
        self.long_conds: list[tuple[str, bool, str]] = []
        self.hl_connected = False
        self.binance_connected = False
        self.bybit_connected = False
        self.console = Console()

    def set_connection_status(self, source: str, connected: bool) -> None:
        """Set connection status for a data source."""
        if source == "hl":
            self.hl_connected = connected
        elif source == "binance":
            self.binance_connected = connected
        elif source == "bybit":
            self.bybit_connected = connected

    def render(self) -> Group:
        """Render the full dashboard."""
        # Side-by-side: market conditions + entry checklists
        conditions = build_conditions_panel(
            self.state.last_bias,
            self.delta_1m,
            self.delta_5m,
            self.buy_pressure,
            self.vwap,
            self.high_5m,
            self.low_5m,
        )

        checklist = build_entry_checklist(self.short_conds, self.long_conds)

        return Group(
            "",
            Align.center(build_header_bar(self.state, self.current_price, self.unrealized_pnl)),
            "",
            Columns([conditions, checklist], expand=True),
            "",
            build_position_panel(self.state.position, self.current_price),
            "",
            build_trade_log(self.today_trades),
            "",
            Panel(
                build_stats_bar(
                    self.state,
                    self.hl_connected,
                    self.binance_connected,
                    self.bybit_connected,
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
