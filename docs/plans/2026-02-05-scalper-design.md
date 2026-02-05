# BTC Scalper Bot Design

## Overview

Automated BTC scalping bot using order flow, liquidation hotzones, and short-term delta. Paper trades on Hyperliquid mark price, logs all trades to SQLite with full market condition snapshots.

## Decisions

- **Exchange**: Hyperliquid (real API, paper trade engine - no real orders)
- **Data Sources**: Existing orderflow (Bybit+Binance), liqlevels (Binance OI), new VWAP + Hyperliquid price
- **VWAP**: Calculate from Binance aggTrade, reset at 00:00 UTC
- **Local high/low**: Rolling 5-minute window
- **Paper Trading**: Local fill simulator using Hyperliquid mark price
- **Trade Log**: SQLite database with full condition snapshots
- **Dashboard**: Rich TUI (4th CLI tool)
- **Notifications**: CLI dashboard only, no Telegram

## Architecture

```
DATA LAYER (existing + new)
├── src/orderflow/    → delta, buy/sell pressure (5m/15m)
├── src/liqlevels/    → liquidation zones, OI, prices
├── NEW: VWAP calculator (from Binance aggTrade)
└── NEW: Hyperliquid price feed (BTC mark price)
        │
STRATEGY ENGINE
├── Bias engine       → SHORT / LONG / NONE
├── Entry validator   → all conditions check
└── Exit monitor      → TP1, TP2, stop, early exit
        │
PAPER TRADE ENGINE
├── Position manager  → tracks open position
├── Fill simulator    → simulates fills at mark price
├── Risk manager      → max trades, daily loss limit
└── Trade logger      → SQLite database
        │
CLI DASHBOARD
└── Bot state, bias, position, P&L, trade log
```

## Data Models

```python
@dataclass
class Bias:
    direction: str          # "LONG" | "SHORT" | "NONE"
    long_liq_usd: float
    short_liq_usd: float
    delta_15m: float
    price_vs_vwap: str      # "ABOVE" | "BELOW"
    timestamp: int

@dataclass
class Position:
    side: str               # "LONG" | "SHORT"
    entry_price: float
    size_usd: float
    leverage: int
    stop_loss: float
    tp1: float
    tp2: float
    tp1_hit: bool
    entry_time: int
    entry_reason: str

@dataclass
class TradeRecord:
    id: int
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
    exit_reason: str        # "TP1" | "TP2" | "STOP" | "EARLY_EXIT"
    duration_seconds: int
    bias: str
    delta_5m: float
    delta_15m: float
    buy_pressure: float
    vwap: float
    long_liq_usd: float
    short_liq_usd: float

@dataclass
class BotState:
    account_balance: float  # Starting $10K paper
    daily_pnl: float
    daily_trades: int
    total_trades: int
    wins: int
    losses: int
    position: Position | None
    is_active: bool
```

## Strategy Logic

### Bias Engine

```python
if long_liq >= 2 * short_liq and delta_15m < 0 and price <= vwap:
    bias = "SHORT"
elif short_liq >= 2 * long_liq and delta_15m > 0 and price >= vwap:
    bias = "LONG"
else:
    bias = "NONE"
```

### Entry Conditions

| Condition | SHORT | LONG |
|-----------|-------|------|
| Bias | SHORT | LONG |
| Price location | 0.1-0.3% of 5m high | 0.3-0.5% of 5m low |
| 5m delta | Negative | Strongly positive |
| Buy pressure | < 50% | > 50% |
| No contrary flow | No large buys 30s | No large sells 30s |
| No open position | True | True |
| Daily limits OK | trades < 6, pnl > -2R | Same |

### Exit Rules

- **Stop Loss**: Entry ± 0.25% → full exit
- **TP1**: Entry ± 0.3% → exit 50%
- **TP2**: Nearest liquidation cluster → exit remaining
- **Early Exit**: 5m delta flips against → exit immediately

## Paper Trade Parameters

| Parameter | Value |
|-----------|-------|
| Initial balance | $10,000 |
| Risk per trade | 1% ($100) |
| Leverage | 15x |
| Max stop | 0.25% |
| Max daily trades | 6 |
| Max daily loss | -2R |
| Strategy loop | 1 second |
| Dashboard refresh | 0.5 second |

## Position Sizing

```
risk_usd = balance * 0.01            # $100
position_usd = risk_usd / 0.0025     # $40,000
margin_required = position_usd / 15   # $2,667
```

## SQLite Schema

```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    side TEXT, entry_price REAL, exit_price REAL,
    size_usd REAL, leverage INTEGER,
    pnl_usd REAL, pnl_percent REAL, r_multiple REAL,
    entry_time INTEGER, exit_time INTEGER, duration_s INTEGER,
    exit_reason TEXT, bias TEXT,
    delta_5m REAL, delta_15m REAL, buy_pressure REAL,
    vwap REAL, long_liq_usd REAL, short_liq_usd REAL
);
```

## File Structure

```
src/scalper/
├── __init__.py
├── models.py          # Bias, Position, TradeRecord, BotState
├── vwap.py            # VWAP calculator
├── hyperliquid.py     # HyperLiquid price feed
├── strategy.py        # Bias engine + entry/exit logic
├── paper_engine.py    # Position manager, fill sim, risk manager
├── database.py        # SQLite trade logger
├── ui/
│   ├── __init__.py
│   └── dashboard.py   # Rich TUI dashboard
└── main.py            # Entry point
```

## CLI Command

```bash
uv run scalper
```
