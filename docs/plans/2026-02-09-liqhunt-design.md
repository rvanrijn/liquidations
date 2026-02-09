# Liquidation Hunt Signal Engine — Design

## Overview

Post-liquidation trade signal engine for BTC perpetuals on 5-minute timeframe. Uses the Magnet Monitor as a bias engine to identify high-probability setups after forced liquidation sweeps.

Core thesis: the side with bigger estimated liquidation USD acts as a magnet. After price sweeps that side and liquidations fire, enter in the reversal direction once price reclaims the magnet level.

## Architecture Decisions

- **Separate module** `src/liqhunt/` — imports magnet data from `liqlevels`, owns its own candle fetching and signal logic
- **Phased delivery** — Phase 1: signal-only dashboard. Phase 2: paper trading engine
- **Binance REST klines** — Poll `/fapi/v1/klines?interval=5m&limit=50` each 10s cycle, cached within same 5m window
- **Single process** — One `main.py` creates `BinanceLiqClient`, `MagnetMonitor`, `CandleFetcher`, and `SignalEngine` in one loop. Entry point: `uv run liqhunt`

## Module Structure

```
src/liqhunt/
├── __init__.py
├── main.py            # Entry point — single loop
├── models.py          # Signal, Candle, SweepResult
├── candles.py         # Binance 5m kline fetcher + rolling stats
├── sweep.py           # Sweep validation (5 criteria, need 3)
├── signal_engine.py   # Core decision engine
└── dashboard.py       # Rich TUI with signal + sweep panels
```

## Data Flow (per 10s tick)

```
BinanceLiqClient.get_all_coins()
    ├─→ MagnetMonitor.update(btc_data)   → snapshot, battles
    ├─→ CandleFetcher.fetch()            → latest 5m candles
    └─→ SignalEngine.evaluate(snapshot, candles, btc_price)
            └─→ Signal | None → Dashboard.render()
```

## Models

### Candle
- OHLCV + timestamp
- Properties: range, body, upper_wick, lower_wick, wick_ratio, is_bullish

### SweepResult
- valid, candle, extreme (wick tip), direction ("DOWN"/"UP"), criteria_met

### Signal
- direction, entry_price, stop_price, risk_pct
- primary_target, secondary_target
- reasoning, timestamp, magnet_side, imbalance_ratio, sweep

## Candle Fetcher

- Endpoint: `/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=50`
- 50 candles = ~4h context. Last candle is in-progress (excluded from analysis)
- `avg_range`: rolling mean of last 20 closed candle ranges
- Caches within same 5m window to avoid redundant API calls
- Shares `aiohttp.ClientSession` from `BinanceLiqClient`

## Sweep Validation

Constants: `RANGE_MULTIPLIER=1.2`, `WICK_MIN_RATIO=0.4`, `ZONE_PCT=0.004`, `MIN_CRITERIA=3`

Five criteria (need >= 3):
1. Candle range >= 1.2x average range
2. Wick >= 40% of total candle range
3. Candle closes away from the extreme
4. Price trades through >= 1 liquidation level
5. Next candle fails to continue in sweep direction

Returns `None` if price never entered ±0.4% execution zone.

## Signal Engine

Decision tree (top to bottom, first failure = NO TRADE):
1. Bias gate — snapshot exists, imbalance >= 1.5x
2. Sweep validation — valid sweep confirmed
3. Execution zone — sweep entered ±0.4% of magnet, not slow drift
4. Reclaim-and-hold — candle closed back beyond magnet, next candle held
5. Risk check — stop beyond sweep extreme, risk <= 0.6%
6. Targets — primary: nearest opposite liq cluster, secondary: range extreme or opposite magnet
7. Hard filters — no multiple failed sweeps in same direction

State: last signal timestamp (dedup), last sweep direction, failed sweep count.

Magnet flip exception: if magnet flips within 3 candles of a sweep and price holds beyond extreme, switch to momentum mode — enter on pullback to VWAP.

## Dashboard

Two new panels added to liqlevels layout:

**Signal Status** — Shows active signal (direction, entry, SL, TP1, TP2, reasoning) or "NO TRADE" with specific reason (insufficient imbalance, invalid sweep, waiting for reclaim, etc.)

**Sweep Monitor** — Current 5m candle stats vs thresholds: range vs avg, wick %, zone distance.

## Entry Point

`uv run liqhunt` — registered in `pyproject.toml` as `liqhunt = "src.liqhunt.main:main"`

## Implementation Order

1. models.py — pure dataclasses
2. candles.py — kline fetcher + Candle parsing
3. sweep.py — validation logic (pure function)
4. signal_engine.py — decision tree
5. dashboard.py — Rich TUI panels
6. main.py — loop wiring + pyproject.toml entry point
