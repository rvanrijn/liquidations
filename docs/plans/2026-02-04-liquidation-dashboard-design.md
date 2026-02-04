# Liquidation Dashboard Design

## Overview

A CLI dashboard that displays real-time cryptocurrency liquidation data from multiple exchanges (Bybit, Binance) with a rich terminal UI.

## Data Sources

| Exchange | Feed | URL | Update Rate |
|----------|------|-----|-------------|
| Bybit | `allLiquidation.{symbol}` | `wss://stream.bybit.com/v5/public/linear` | 500ms |
| Binance | `{symbol}@forceOrder` | `wss://fstream.binance.com/stream` | 1000ms |

## Architecture

```
┌──────────────┐    ┌──────────────┐
│ Bybit WS     │    │ Binance WS   │
│ Client       │    │ Client       │
└──────┬───────┘    └──────┬───────┘
       │                   │
       └─────────┬─────────┘
                 ▼
       ┌──────────────────┐
       │ Aggregator       │
       │ (24h rolling)    │
       └────────┬─────────┘
                │
                ▼
       ┌──────────────────┐
       │ Rich TUI         │
       │ Dashboard        │
       └──────────────────┘
```

## Data Model

```python
@dataclass
class LiquidationEvent:
    exchange: str        # "bybit" | "binance"
    coin: str            # "BTC", "ETH", etc.
    side: str            # "long" | "short"
    size: float          # Position size
    price: float         # Liquidation price
    value_usd: float     # Total USD value
    timestamp: int       # Unix ms
    wallet: str | None   # Address if available
```

## Dashboard Panels

1. **Long/Short Ratio Bar** - Visual bar showing long vs short dominance
2. **Top 10 Largest Liquidations** - Table with Value, Coin, Side, Price, Wallet, Time
3. **Liquidations by Coin** - Summary with Count, Total Value, Long $, Short $, Exchange breakdown
4. **Live Feed** - Scrolling real-time liquidations (last 15)
5. **Status Bar** - Connection status, 24h total, exit hint

## Configuration

| Setting | Value |
|---------|-------|
| Time window | 24 hours (fixed) |
| Minimum value | $1,000 USD |
| Coins | BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, LINK, DOT, MATIC, UNI, LTC, BCH, ATOM, APT |
| Refresh rate | 500ms |

## Project Structure

```
src/
├── __init__.py
├── main.py              # Entry point
├── models.py            # LiquidationEvent dataclass
├── aggregator.py        # Rolling 24h window, stats
├── clients/
│   ├── __init__.py
│   ├── base.py          # Abstract WebSocket client
│   ├── bybit.py         # Bybit client
│   └── binance.py       # Binance client
└── ui/
    ├── __init__.py
    └── dashboard.py     # Rich TUI
```

## Dependencies

```
websockets
rich
python-dotenv
```

## Color Coding

- LONG: Green
- SHORT: Red
- Bybit: Yellow
- Binance: Cyan
- Large liquidations (>$100K): Bold

## Connection Handling

- Auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s)
- Graceful shutdown on Ctrl+C
- Status indicators for each exchange

## Future Enhancements (Out of Scope)

- Telegram alerts for large liquidations
- Hyperliquid integration via CoinGlass API
- Historical data persistence
- Configurable time windows and filters
