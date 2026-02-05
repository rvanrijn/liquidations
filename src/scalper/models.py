# src/scalper/models.py
"""Data models for the scalping bot."""

from dataclasses import dataclass, field
from time import time


@dataclass
class Bias:
    """Directional bias calculated from market conditions."""
    direction: str  # "LONG" | "SHORT" | "NONE"
    long_liq_usd: float
    short_liq_usd: float
    delta_15m: float
    price_vs_vwap: str  # "ABOVE" | "BELOW"
    timestamp: int = field(default_factory=lambda: int(time() * 1000))


@dataclass
class Position:
    """An open paper trade position."""
    side: str  # "LONG" | "SHORT"
    entry_price: float
    size_usd: float
    leverage: int
    stop_loss: float
    tp1: float
    tp2: float
    tp1_hit: bool = False
    remaining_pct: float = 1.0  # 1.0 = full, 0.5 = after TP1
    entry_time: int = field(default_factory=lambda: int(time() * 1000))
    entry_reason: str = ""


@dataclass
class TradeRecord:
    """A completed trade stored in SQLite."""
    side: str
    entry_price: float
    exit_price: float
    size_usd: float
    leverage: int
    pnl_usd: float
    pnl_percent: float
    r_multiple: float
    entry_time: int
    exit_time: int
    exit_reason: str  # "TP1" | "TP2" | "STOP" | "EARLY_EXIT"
    duration_seconds: int
    bias: str
    delta_5m: float
    delta_15m: float
    buy_pressure: float
    vwap: float
    long_liq_usd: float
    short_liq_usd: float
    id: int | None = None


@dataclass
class BotState:
    """Current state of the scalping bot."""
    account_balance: float = 10_000.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    position: Position | None = None
    is_active: bool = True
    last_bias: Bias | None = None

    # Config
    initial_balance: float = 10_000.0
    risk_per_trade: float = 0.01  # 1%
    leverage: int = 15
    max_stop_pct: float = 0.0025  # 0.25%
    max_daily_trades: int = 6
    max_daily_loss_r: float = -2.0

    @property
    def risk_usd(self) -> float:
        """Dollar amount risked per trade."""
        return self.account_balance * self.risk_per_trade

    @property
    def position_size_usd(self) -> float:
        """Total position size in USD."""
        return self.risk_usd / self.max_stop_pct

    @property
    def win_rate(self) -> float:
        """Win rate as percentage."""
        if self.total_trades == 0:
            return 0.0
        return (self.wins / self.total_trades) * 100
