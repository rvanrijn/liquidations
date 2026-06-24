# Battle Trader Visualizer Design

## Goal

Interactive web-based visualizer to verify current battle trader filter rules and test new ones against historical battle data.

## Stack

- **Backend**: FastAPI + asyncssh + aiosqlite
- **Frontend**: TradingView Lightweight Charts, vanilla JS
- **Data**: krakenbot.db synced from VPS via SSH

## Architecture

```
Browser (localhost:8099)
  ├── Price chart (candlesticks + entry/exit markers)
  ├── OI overlay (line chart)
  ├── Filter controls panel (sliders, toggles, checkboxes)
  └── Stats summary (WR, PnL, trade count, equity curve)
        │
        ▼
FastAPI backend
  ├── GET /api/battles — all battles with filter annotations
  ├── GET /api/candles — market_samples resampled to 1m OHLC
  ├── GET /api/simulate — apply filter params, return stats
  └── SSH sync to VPS → local cached copy of krakenbot.db
```

## UI Layout

**Top (60%): Price Chart**
- BTC candlesticks (1m, resampled from market_samples)
- Green/red markers for entry/exit on winning/losing trades
- OI change % line overlay on secondary axis
- Click trade in table → chart scrolls to that battle

**Left sidebar: Filter Controls**
- OI Gate: slider -0.10% to -1.00% (default -0.35%)
- Cooldown: slider 0-60 min (default 20m)
- Skip Hours: checkboxes 0-23 (default {7,8,15,18})
- Min Imbalance: slider 1.0-3.0 (default 1.25)
- HA Min Streak: 1-5 (default 2)
- HA Min Body Ratio: 0.0-1.0 (default 0.3)
- OI Deep Threshold: -0.20% to -1.00% (default -0.45%)
- Apply button → re-simulates

**Bottom (40%): Results Panel**
- Summary bar: trades / WR / PnL / avg win / avg loss / profit factor
- Trade table: timestamp, direction, entry, exit, move%, PnL, reason, filter pass/fail
- Equity curve (small line chart)
- Filtered-out trades shown in grey with blocking filter name

## Backend Details

**VPS sync**: asyncssh copies krakenbot.db to local temp path on startup + refresh button. No persistent connection.

**Simulation engine**: Takes battles + filter params, replays chronologically:
- Check cooldown from last trade exit
- Check skip hour
- Check OI gate threshold
- Check imbalance ratio
- Check HA signal (if available)
- Compute PnL with maker 0.02% + taker 0.05% fees
- Return kept/filtered trades with reasons + summary stats

**Candle resampling**: market_samples (~17s intervals) resampled to 1m OHLC.

## Schema Change

Add HA signal columns to battles table in krakenbot:
- `ha_color` TEXT (RED/GREEN/empty)
- `ha_body_ratio` REAL
- `ha_streak` INTEGER

Old battles: NULL (shown as "HA unknown" in visualizer).

## Files

- `src/visualizer/__init__.py`
- `src/visualizer/app.py` — FastAPI backend + simulation engine
- `src/visualizer/static/index.html`
- `src/visualizer/static/app.js`
- `src/visualizer/static/style.css`

## Dependencies

fastapi, uvicorn, asyncssh, aiosqlite

## Run

```bash
uv run uvicorn src.visualizer.app:app --port 8099
```
