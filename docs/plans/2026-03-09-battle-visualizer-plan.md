# Battle Trader Visualizer — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Interactive web app to replay battle trader trades on a price chart, toggle filter rules, and instantly see impact on WR/PnL.

**Architecture:** FastAPI backend syncs krakenbot.db from VPS via asyncssh, serves 1m candles + battles + simulation results. Single-page frontend uses TradingView Lightweight Charts for price/OI display and vanilla JS for filter controls.

**Tech Stack:** FastAPI, uvicorn, asyncssh, aiosqlite, TradingView Lightweight Charts (CDN)

---

## Task 1: Dependencies + skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `src/visualizer/__init__.py`
- Create: `src/visualizer/app.py`

**Step 1: Add dependencies to pyproject.toml**

Add to the `dependencies` list in `pyproject.toml`:
```
"fastapi>=0.115",
"uvicorn>=0.34",
"asyncssh>=2.17",
"aiosqlite>=0.20",
```

Add entry point in `[project.scripts]`:
```
visualizer = "src.visualizer.app:main"
```

**Step 2: Create empty __init__.py**

```python
# src/visualizer/__init__.py
```

**Step 3: Create minimal FastAPI app**

```python
# src/visualizer/app.py
"""Battle Trader Visualizer — FastAPI backend."""

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI(title="Battle Visualizer")

STATIC_DIR = Path(__file__).parent / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def main():
    uvicorn.run("src.visualizer.app:app", host="0.0.0.0", port=8099, reload=True)


if __name__ == "__main__":
    main()
```

**Step 4: Create static directory**

```bash
mkdir -p src/visualizer/static
```

**Step 5: Verify it starts**

```bash
uv sync && uv run python -c "from src.visualizer.app import app; print('OK')"
```

**Step 6: Commit**

```bash
git add src/visualizer/ pyproject.toml
git commit -m "feat(visualizer): add FastAPI skeleton + dependencies"
```

---

## Task 2: VPS DB sync

**Files:**
- Modify: `src/visualizer/app.py`

**Step 1: Add SSH sync module**

Add to `app.py` — a function that copies krakenbot.db from VPS to `data/visualizer_cache/krakenbot.db` via asyncssh SCP.

```python
import asyncssh
import os
from pathlib import Path

VPS_HOST = "16.171.155.223"
VPS_USER = "ubuntu"
SSH_KEY = os.path.expanduser("~/.ssh/kraken-bot-ssh-key.pem")
REMOTE_DB = "~/kraken-bot/data/krakenbot.db"
REMOTE_DB_BAK = "~/kraken-bot/data/krakenbot.db.bak.20260309"
CACHE_DIR = Path("data/visualizer_cache")
LOCAL_DB = CACHE_DIR / "krakenbot.db"
LOCAL_DB_BAK = CACHE_DIR / "krakenbot_bak.db"


async def sync_db():
    """Copy krakenbot.db (+ backup) from VPS to local cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    async with asyncssh.connect(
        VPS_HOST, username=VPS_USER,
        client_keys=[SSH_KEY], known_hosts=None,
    ) as conn:
        await asyncssh.scp((conn, REMOTE_DB), str(LOCAL_DB))
        # Also grab the backup with the 294 historical battles
        try:
            await asyncssh.scp((conn, REMOTE_DB_BAK), str(LOCAL_DB_BAK))
        except Exception:
            pass  # backup may not exist
    return {
        "db": str(LOCAL_DB),
        "db_bak": str(LOCAL_DB_BAK) if LOCAL_DB_BAK.exists() else None,
    }
```

**Step 2: Add sync endpoint**

```python
@app.post("/api/sync")
async def sync():
    """Sync DB from VPS."""
    result = await sync_db()
    return {"status": "synced", **result}
```

**Step 3: Add startup sync**

```python
@app.on_event("startup")
async def startup():
    try:
        await sync_db()
    except Exception as e:
        print(f"Warning: VPS sync failed on startup: {e}")
        print("Using cached DB if available.")
```

**Step 4: Verify sync works**

```bash
uv run python -c "
import asyncio
from src.visualizer.app import sync_db
result = asyncio.run(sync_db())
print(result)
"
```

**Step 5: Commit**

```bash
git add src/visualizer/app.py
git commit -m "feat(visualizer): add VPS DB sync via asyncssh"
```

---

## Task 3: Data API endpoints

**Files:**
- Modify: `src/visualizer/app.py`

**Step 1: Add battles endpoint**

Reads from both current DB and backup DB. Returns all battles as JSON.

```python
import aiosqlite
from datetime import datetime, timezone


async def _get_db_path() -> str:
    """Return best available DB path (prefer backup with more data)."""
    if LOCAL_DB_BAK.exists():
        return str(LOCAL_DB_BAK)
    if LOCAL_DB.exists():
        return str(LOCAL_DB)
    raise FileNotFoundError("No DB found. Run /api/sync first.")


@app.get("/api/battles")
async def get_battles():
    """Return all battles from both DBs, deduplicated by timestamp."""
    battles = []
    for db_path in [LOCAL_DB_BAK, LOCAL_DB]:
        if not db_path.exists():
            continue
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM battles ORDER BY timestamp"
            )
            for r in rows:
                battles.append(dict(r))

    # Deduplicate by timestamp (battles may overlap between DBs)
    seen = set()
    unique = []
    for b in battles:
        key = round(b["timestamp"], 1)
        if key not in seen:
            seen.add(key)
            unique.append(b)

    unique.sort(key=lambda b: b["timestamp"])
    return {"battles": unique, "count": len(unique)}
```

**Step 2: Add candles endpoint**

Resample market_samples into 1m OHLC candles.

```python
@app.get("/api/candles")
async def get_candles():
    """Return 1m OHLC candles from market_samples."""
    candles = []
    for db_path in [LOCAL_DB_BAK, LOCAL_DB]:
        if not db_path.exists():
            continue
        async with aiosqlite.connect(str(db_path)) as db:
            rows = await db.execute_fetchall(
                "SELECT timestamp, btc_price, oi_usd, oi_change_pct "
                "FROM market_samples ORDER BY timestamp"
            )
            for r in rows:
                candles.append(r)

    if not candles:
        return {"candles": [], "oi": []}

    # Deduplicate by timestamp
    seen = set()
    unique = []
    for c in candles:
        key = round(c[0], 0)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    unique.sort(key=lambda c: c[0])

    # Resample to 1m OHLC
    ohlc = []
    oi_data = []
    bucket_start = None
    o = h = l = c_price = 0.0

    for ts, price, oi_usd, oi_pct in unique:
        minute = int(ts) // 60 * 60  # floor to minute
        if bucket_start is None or minute != bucket_start:
            if bucket_start is not None:
                ohlc.append({
                    "time": bucket_start,
                    "open": o, "high": h, "low": l, "close": c_price,
                })
            bucket_start = minute
            o = h = l = c_price = price
        else:
            h = max(h, price)
            l = min(l, price)
            c_price = price

        oi_data.append({"time": int(ts), "value": oi_pct})

    # Last bucket
    if bucket_start is not None:
        ohlc.append({
            "time": bucket_start,
            "open": o, "high": h, "low": l, "close": c_price,
        })

    # Thin out OI data (every 60s max)
    oi_thinned = []
    last_oi_time = 0
    for d in oi_data:
        if d["time"] - last_oi_time >= 60:
            oi_thinned.append({"time": d["time"], "value": round(d["value"], 4)})
            last_oi_time = d["time"]

    return {"candles": ohlc, "oi": oi_thinned}
```

**Step 3: Verify endpoints**

```bash
uv run uvicorn src.visualizer.app:app --port 8099 &
sleep 2
curl -s http://localhost:8099/api/battles | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['count'], 'battles')"
curl -s http://localhost:8099/api/candles | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['candles']), 'candles')"
kill %1
```

**Step 4: Commit**

```bash
git add src/visualizer/app.py
git commit -m "feat(visualizer): add battles + candles API endpoints"
```

---

## Task 4: Simulation engine

**Files:**
- Modify: `src/visualizer/app.py`

**Step 1: Add simulation logic**

```python
from pydantic import BaseModel
from typing import Optional


class FilterParams(BaseModel):
    oi_gate_pct: float = -0.35
    cooldown_min: int = 20
    skip_hours: list[int] = [7, 8, 15, 18]
    min_imbalance: float = 1.25
    ha_min_streak: int = 2
    ha_min_body_ratio: float = 0.3
    oi_deep_threshold: float = -0.45
    leverage: float = 5.0
    starting_balance: float = 5000.0
    maker_fee_pct: float = 0.02
    taker_fee_pct: float = 0.05


def simulate(battles: list[dict], params: FilterParams) -> dict:
    """Replay battles through filter rules, return trades + stats."""
    balance = params.starting_balance
    trades = []
    last_exit_time = 0.0
    cooldown_sec = params.cooldown_min * 60

    for b in battles:
        ts = b["timestamp"]
        entry_hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        oi_pct = b.get("oi_change_pct") or 0.0
        imb = b.get("imbalance_ratio") or 0.0
        bigger_side = b.get("bigger_side", "")
        correct = b.get("hypothesis_correct", 0)
        move_pct = b.get("move_pct", 0.0)
        duration = b.get("duration_seconds", 0.0)

        # Direction: LONG magnet = SHORT trade
        direction = "SHORT" if bigger_side == "LONG" else "LONG"

        # Check filters
        filters_failed = []

        # Cooldown
        if last_exit_time > 0 and (ts - last_exit_time) < cooldown_sec:
            gap_min = (ts - last_exit_time) / 60
            filters_failed.append(f"cooldown ({gap_min:.0f}m < {params.cooldown_min}m)")

        # Skip hours
        if entry_hour in params.skip_hours:
            filters_failed.append(f"skip_hour ({entry_hour} UTC)")

        # OI gate
        if oi_pct > params.oi_gate_pct:
            filters_failed.append(f"oi_gate ({oi_pct:.2f}% > {params.oi_gate_pct:.2f}%)")

        # Imbalance
        if imb < params.min_imbalance:
            filters_failed.append(f"imbalance ({imb:.2f}x < {params.min_imbalance:.1f}x)")

        # HA filter (only if data available)
        ha_color = b.get("ha_color")
        ha_body = b.get("ha_body_ratio")
        ha_streak = b.get("ha_streak")
        ha_available = ha_color is not None and ha_color != ""

        if ha_available:
            aligned = (direction == "SHORT" and ha_color == "RED") or \
                      (direction == "LONG" and ha_color == "GREEN")
            if not aligned:
                filters_failed.append(f"ha_color ({ha_color} vs {direction})")
            if ha_streak is not None and ha_streak < params.ha_min_streak:
                filters_failed.append(f"ha_streak ({ha_streak} < {params.ha_min_streak})")
            if ha_body is not None and ha_body < params.ha_min_body_ratio:
                filters_failed.append(f"ha_body ({ha_body:.2f} < {params.ha_min_body_ratio:.1f})")

        passed = len(filters_failed) == 0
        notional = balance * params.leverage if passed else 0.0

        # PnL calc for passing trades
        pnl = 0.0
        exit_reason = ""
        if passed:
            # Trade direction: if hypothesis correct, we win the move
            if correct:
                trade_move = abs(move_pct)
            else:
                trade_move = -abs(move_pct)

            fee = notional * (params.maker_fee_pct + params.taker_fee_pct) / 100
            pnl = trade_move / 100 * notional - fee
            balance += pnl
            exit_time = ts + duration
            last_exit_time = exit_time
            exit_reason = "battle_resolved"

        trade = {
            "timestamp": ts,
            "entry_time_str": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M"),
            "direction": direction,
            "bigger_side": bigger_side,
            "entry_price": b.get("snapshot_price", 0),
            "exit_price": b.get("resolved_price", 0),
            "move_pct": move_pct,
            "oi_change_pct": oi_pct,
            "imbalance_ratio": imb,
            "hypothesis_correct": bool(correct),
            "passed": passed,
            "filters_failed": filters_failed,
            "pnl": round(pnl, 2),
            "balance_after": round(balance, 2),
            "duration_sec": duration,
            "exit_reason": exit_reason,
            "ha_color": ha_color,
            "ha_body_ratio": ha_body,
            "ha_streak": ha_streak,
        }
        trades.append(trade)

    # Summary stats
    kept = [t for t in trades if t["passed"]]
    wins = [t for t in kept if t["pnl"] > 0]
    losses = [t for t in kept if t["pnl"] < 0]
    be = [t for t in kept if t["pnl"] == 0]

    total_pnl = sum(t["pnl"] for t in kept)
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))

    return {
        "trades": trades,
        "summary": {
            "total_battles": len(trades),
            "trades_taken": len(kept),
            "trades_filtered": len(trades) - len(kept),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(be),
            "win_rate": round(len(wins) / len(kept) * 100, 1) if kept else 0,
            "win_rate_excl_be": round(len(wins) / (len(wins) + len(losses)) * 100, 1) if (wins or losses) else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(gross_win / len(wins), 2) if wins else 0,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 999,
            "final_balance": round(balance, 2),
            "max_drawdown": round(_calc_max_dd(kept), 2),
        },
    }


def _calc_max_dd(trades: list[dict]) -> float:
    """Calculate max drawdown from equity curve."""
    if not trades:
        return 0.0
    peak = trades[0]["balance_after"] - trades[0]["pnl"]  # starting balance
    max_dd = 0.0
    for t in trades:
        bal = t["balance_after"]
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return max_dd
```

**Step 2: Add simulate endpoint**

```python
@app.post("/api/simulate")
async def simulate_endpoint(params: FilterParams):
    """Run simulation with given filter params."""
    # Load all battles
    battles_resp = await get_battles()
    battles = battles_resp["battles"]
    result = simulate(battles, params)
    return result
```

**Step 3: Verify simulation**

```bash
curl -s -X POST http://localhost:8099/api/simulate \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d['summary'], indent=2))"
```

**Step 4: Commit**

```bash
git add src/visualizer/app.py
git commit -m "feat(visualizer): add simulation engine + endpoint"
```

---

## Task 5: Frontend — HTML shell + chart

**Files:**
- Create: `src/visualizer/static/index.html`
- Create: `src/visualizer/static/style.css`
- Create: `src/visualizer/static/app.js`

**Step 1: Create index.html**

Single-page layout: sidebar (filters) + main area (chart top, results bottom). Load Lightweight Charts from CDN. Load app.js and style.css.

Key structure:
```html
<!DOCTYPE html>
<html>
<head>
    <title>Battle Visualizer</title>
    <link rel="stylesheet" href="/static/style.css">
    <script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
    <div id="sidebar">
        <!-- Filter controls: sliders, checkboxes, apply button -->
        <h2>Filters</h2>
        <div class="filter-group">
            <label>OI Gate %</label>
            <input type="range" id="oi-gate" min="-100" max="-10" value="-35">
            <span id="oi-gate-val">-0.35%</span>
        </div>
        <div class="filter-group">
            <label>Cooldown (min)</label>
            <input type="range" id="cooldown" min="0" max="60" value="20">
            <span id="cooldown-val">20m</span>
        </div>
        <div class="filter-group">
            <label>Min Imbalance</label>
            <input type="range" id="min-imbalance" min="100" max="300" value="125">
            <span id="min-imbalance-val">1.25x</span>
        </div>
        <div class="filter-group">
            <label>HA Min Streak</label>
            <input type="range" id="ha-streak" min="1" max="5" value="2">
            <span id="ha-streak-val">2</span>
        </div>
        <div class="filter-group">
            <label>HA Body Ratio</label>
            <input type="range" id="ha-body" min="0" max="100" value="30">
            <span id="ha-body-val">0.30</span>
        </div>
        <div class="filter-group">
            <label>OI Deep Threshold</label>
            <input type="range" id="oi-deep" min="-100" max="-20" value="-45">
            <span id="oi-deep-val">-0.45%</span>
        </div>
        <div class="filter-group">
            <label>Skip Hours (UTC)</label>
            <div id="skip-hours">
                <!-- 24 small checkboxes, 7/8/15/18 checked by default -->
            </div>
        </div>
        <button id="apply-btn">Apply Filters</button>
        <button id="sync-btn">Sync VPS</button>

        <div id="summary">
            <!-- Populated by JS -->
        </div>
    </div>
    <div id="main">
        <div id="chart-container"></div>
        <div id="results">
            <div id="equity-container"></div>
            <table id="trades-table">
                <thead>
                    <tr>
                        <th>Time</th><th>Dir</th><th>Entry</th><th>Exit</th>
                        <th>Move%</th><th>PnL</th><th>OI%</th><th>Imb</th>
                        <th>Reason</th><th>Filters</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </div>
    <script src="/static/app.js"></script>
</body>
</html>
```

**Step 2: Create style.css**

Dark theme (trading app feel). Sidebar 280px fixed left. Chart fills top 60%. Table scrollable bottom 40%. Green/red for win/loss. Grey for filtered trades.

**Step 3: Create app.js**

Core logic:
1. On load: fetch `/api/candles`, create Lightweight Chart with candlestick + OI line series
2. On load: POST `/api/simulate` with default params, populate table + markers + summary + equity curve
3. Slider changes: update display values
4. Apply button: POST `/api/simulate` with current slider values, re-render table + markers + equity
5. Table row click: `chart.timeScale().scrollToPosition()` to that trade's timestamp
6. Sync button: POST `/api/sync`, then reload data

Markers: green upward triangle for winning entry, red downward for losing entry. Grey circle for filtered battles.

Equity curve: separate small Lightweight Chart (line series) below the trade table.

**Step 4: Commit**

```bash
git add src/visualizer/static/
git commit -m "feat(visualizer): add frontend with chart, filters, and trade table"
```

---

## Task 6: Serve index.html at root

**Files:**
- Modify: `src/visualizer/app.py`

**Step 1: Add root redirect**

```python
from fastapi.responses import FileResponse

@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))
```

**Step 2: Verify full app**

```bash
uv run uvicorn src.visualizer.app:app --port 8099
# Open http://localhost:8099 in browser
# Should see chart with candles, markers, filter sidebar, trade table
```

**Step 3: Commit**

```bash
git add src/visualizer/app.py
git commit -m "feat(visualizer): serve index.html at root"
```

---

## Task 7: Add HA columns to krakenbot battles table

**Files:**
- Modify: `src/krakenbot/database.py`
- Modify: `src/krakenbot/monitor.py` (where battles are logged)

**Step 1: Add ALTER TABLE migration**

In `database.py` `_create_tables()`, after the CREATE TABLE block, add migration:

```python
# Add HA columns if they don't exist
for col, typ in [("ha_color", "TEXT"), ("ha_body_ratio", "REAL"), ("ha_streak", "INTEGER")]:
    try:
        self.conn.execute(f"ALTER TABLE battles ADD COLUMN {col} {typ}")
    except Exception:
        pass  # column already exists
```

**Step 2: Update log_battle to include HA data**

In `database.py` `log_battle()`, add `ha_color`, `ha_body_ratio`, `ha_streak` to the INSERT statement. The Battle model (in `liq_models.py`) needs these fields added too.

In `src/krakenbot/liq_models.py`, add to the Battle dataclass:
```python
ha_color: str = ""
ha_body_ratio: float = 0.0
ha_streak: int = 0
```

**Step 3: Pass HA data when creating Battle in monitor.py**

In `monitor.py` where `Battle(...)` is constructed, pass the current HA signal values.

**Step 4: Deploy to VPS**

```bash
rsync -avz --delete -e "ssh -i ~/.ssh/kraken-bot-ssh-key.pem" \
  src/krakenbot/ ubuntu@16.171.155.223:~/kraken-bot/src/krakenbot/
ssh -i ~/.ssh/kraken-bot-ssh-key.pem ubuntu@16.171.155.223 \
  "sudo systemctl restart liqbot"
```

**Step 5: Commit**

```bash
git add src/krakenbot/database.py src/krakenbot/monitor.py src/krakenbot/liq_models.py
git commit -m "feat(krakenbot): log HA signal data with each battle"
```

---

## Execution Order

1. Task 1 — skeleton (5 min)
2. Task 2 — VPS sync (5 min)
3. Task 3 — data endpoints (10 min)
4. Task 4 — simulation engine (10 min)
5. Task 5 — frontend (20 min)
6. Task 6 — serve root (2 min)
7. Task 7 — HA schema change (10 min)

Tasks 1-6 are sequential (each builds on previous). Task 7 is independent and can be done in parallel.
