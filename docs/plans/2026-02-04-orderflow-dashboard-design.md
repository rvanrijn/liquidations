# Order Flow Dashboard Design

## Overview

Real-time CLI dashboard showing aggregated trade flow from Bybit and Binance futures markets.

## Decisions

- **Data Source**: Aggregated trades stream (executed trades, not order book)
- **Exchanges**: Bybit + Binance only
- **Size Thresholds**: Whale >$1M, Large $100K-$1M, Medium $10K-$100K, Small $1K-$10K
- **Time Window**: 15-minute rolling window
- **Panels**: All 6 (stats bar, by exchange, by size, pressure, live feed, status)

## Architecture

```
src/orderflow/
├── __init__.py
├── models.py          # TradeEvent dataclass
├── aggregator.py      # OrderFlowAggregator (15min window)
├── clients/
│   ├── __init__.py
│   ├── base.py        # BaseTradeClient (reuse pattern)
│   ├── bybit.py       # BybitTradeClient
│   └── binance.py     # BinanceTradeClient
├── ui/
│   ├── __init__.py
│   └── dashboard.py   # OrderFlowDashboard
└── main.py            # Entry point
```

## Data Model

```python
@dataclass
class TradeEvent:
    exchange: str      # "bybit" | "binance"
    coin: str          # "BTC", "ETH", etc.
    side: str          # "buy" | "sell"
    size: float        # Quantity
    price: float
    value_usd: float   # size * price
    timestamp: int     # Unix ms
    size_category: str # "whale" | "large" | "medium" | "small"
```

## Aggregator

### State
- `events: deque[TradeEvent]` - Rolling 15-minute window
- Auto-prune events older than window

### Methods
| Method | Returns | Purpose |
|--------|---------|---------|
| `add_event(event)` | None | Add trade, classify size, auto-prune |
| `by_exchange()` | dict | `{exchange: {buy_usd, sell_usd, delta, count}}` |
| `by_size()` | dict | `{category: {buy_usd, sell_usd, delta, count}}` |
| `totals()` | tuple | `(total_buy_usd, total_sell_usd, delta)` |
| `buy_sell_pressure()` | tuple | `(buy_pct, sell_pct)` |
| `recent_feed(n)` | list | Last n large trades (≥$100K) |

### Size Classification
```python
def _classify_size(value_usd: float) -> str:
    if value_usd >= 1_000_000: return "whale"
    if value_usd >= 100_000: return "large"
    if value_usd >= 10_000: return "medium"
    return "small"
```

Filter: Only trades ≥$1K stored.

## WebSocket Clients

### Bybit
- URL: `wss://stream.bybit.com/v5/public/linear`
- Topic: `publicTrade.{COIN}USDT`
- Requires subscription message

```json
{
    "topic": "publicTrade.BTCUSDT",
    "data": [{
        "s": "BTCUSDT",
        "S": "Buy",
        "v": "0.5",
        "p": "65000.00",
        "T": 1234567890123
    }]
}
```

### Binance
- URL: `wss://fstream.binance.com/stream?streams=btcusdt@aggTrade,...`
- Combined stream, no subscription needed

```json
{
    "stream": "btcusdt@aggTrade",
    "data": {
        "s": "BTCUSDT",
        "p": "65000.00",
        "q": "0.5",
        "m": true,
        "T": 1234567890123
    }
}
```

Note: Binance `m=true` means buyer is maker = **sell** taker order.

## Dashboard Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  ORDERFLOW DASHBOARD                                            │
├─────────────────────────────────────────────────────────────────┤
│  Total: $12.5M  │  Buy: $7.2M  │  Sell: $5.3M  │  Delta: +$1.9M │
├─────────────────────────────────────────────────────────────────┤
│  ORDER FLOW BY EXCHANGE (15M)                                   │
│  Exchange   Buy $      Sell $     Delta      Flow               │
│  BYBIT      $4.2M      $3.1M      +$1.1M     ████████░░░░       │
│  BINANCE    $3.0M      $2.2M      +$0.8M     ███████░░░░░       │
├─────────────────────────────────────────────────────────────────┤
│  ORDER FLOW BY SIZE (15M)                                       │
│  Category   Buy $      Sell $     Delta      Flow               │
│  WHALE      $2.1M      $1.5M      +$0.6M     ██████░░░░         │
│  LARGE      $3.5M      $2.8M      +$0.7M     █████░░░░░         │
│  MEDIUM     $1.2M      $0.8M      +$0.4M     ██████░░░░         │
│  SMALL      $0.4M      $0.2M      +$0.2M     ███████░░░         │
├─────────────────────────────────────────────────────────────────┤
│  BUY PRESSURE  ████████████████████░░░░░░░░░░  SELL PRESSURE    │
│                      (58% / 42%)                                │
├─────────────────────────────────────────────────────────────────┤
│  LIVE FEED (Large Trades)                                       │
│  12:34:56  BYBIT   BTC   BUY    $1.2M    $65,432.10            │
│  12:34:52  BINANCE ETH   SELL   $523K    $3,421.50             │
├─────────────────────────────────────────────────────────────────┤
│  Connected: BYBIT ● BINANCE ●  │  Trades: 1,234  │  Ctrl+C exit │
└─────────────────────────────────────────────────────────────────┘
```

### Colors
- Buy/positive delta: green
- Sell/negative delta: red
- Bybit: yellow
- Binance: cyan
- Whale trades: bold yellow in feed

## Entry Point

```python
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "MATIC"]
```

CLI command: `orderflow`

## Tests

- `tests/orderflow/test_models.py` - TradeEvent creation
- `tests/orderflow/test_aggregator.py` - Aggregation, pruning, categorization
- `tests/orderflow/test_clients.py` - Message parsing
