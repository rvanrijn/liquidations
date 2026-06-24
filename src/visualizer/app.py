"""Battle Trader Visualizer — FastAPI backend."""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
import asyncssh
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Battle Visualizer")

STATIC_DIR = Path(__file__).parent / "static"
VPS_HOST = "16.171.155.223"
VPS_USER = "ubuntu"
SSH_KEY = os.path.expanduser("~/.ssh/kraken-bot-ssh-key.pem")
REMOTE_DB = "~/kraken-bot/data/krakenbot.db"
REMOTE_DB_BAK = "~/kraken-bot/data/krakenbot.db.bak.20260309"
CACHE_DIR = Path("data/visualizer_cache")
LOCAL_DB = CACHE_DIR / "krakenbot.db"
LOCAL_DB_BAK = CACHE_DIR / "krakenbot_bak.db"


# --- Models ---

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


# --- VPS Sync ---

async def sync_db():
    """Copy krakenbot.db (+ backup) from VPS to local cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    async with asyncssh.connect(
        VPS_HOST, username=VPS_USER,
        client_keys=[SSH_KEY], known_hosts=None,
    ) as conn:
        await asyncssh.scp((conn, REMOTE_DB), str(LOCAL_DB))
        try:
            await asyncssh.scp((conn, REMOTE_DB_BAK), str(LOCAL_DB_BAK))
        except Exception:
            pass
    return {
        "db": str(LOCAL_DB),
        "db_bak": str(LOCAL_DB_BAK) if LOCAL_DB_BAK.exists() else None,
    }


# --- Data loading ---

async def _load_battles() -> list[dict]:
    """Load all battles from both DBs, deduplicated by timestamp."""
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

    seen = set()
    unique = []
    for b in battles:
        key = round(b["timestamp"], 1)
        if key not in seen:
            seen.add(key)
            unique.append(b)
    unique.sort(key=lambda b: b["timestamp"])
    return unique


async def _load_candles() -> tuple[list[dict], list[dict]]:
    """Load market_samples, resample to 1m OHLC + OI series."""
    raw = []
    for db_path in [LOCAL_DB_BAK, LOCAL_DB]:
        if not db_path.exists():
            continue
        async with aiosqlite.connect(str(db_path)) as db:
            rows = await db.execute_fetchall(
                "SELECT timestamp, btc_price, oi_usd, oi_change_pct "
                "FROM market_samples ORDER BY timestamp"
            )
            raw.extend(rows)

    if not raw:
        return [], []

    # Deduplicate
    seen = set()
    unique = []
    for r in raw:
        key = round(r[0], 0)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    unique.sort(key=lambda r: r[0])

    # Resample to 1m OHLC
    ohlc = []
    oi_data = []
    bucket_start = None
    o = h = l = c_price = 0.0

    for ts, price, oi_usd, oi_pct in unique:
        minute = int(ts) // 60 * 60
        if bucket_start is None or minute != bucket_start:
            if bucket_start is not None:
                ohlc.append({
                    "time": bucket_start,
                    "open": round(o, 1),
                    "high": round(h, 1),
                    "low": round(l, 1),
                    "close": round(c_price, 1),
                })
            bucket_start = minute
            o = h = l = c_price = price
        else:
            h = max(h, price)
            l = min(l, price)
            c_price = price

        oi_data.append({"time": int(ts), "value": round(oi_pct, 4) if oi_pct else 0})

    if bucket_start is not None:
        ohlc.append({
            "time": bucket_start,
            "open": round(o, 1),
            "high": round(h, 1),
            "low": round(l, 1),
            "close": round(c_price, 1),
        })

    # Thin OI to ~1 per minute
    oi_thinned = []
    last_t = 0
    for d in oi_data:
        if d["time"] - last_t >= 60:
            oi_thinned.append(d)
            last_t = d["time"]

    return ohlc, oi_thinned


# --- Simulation ---

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

        direction = "SHORT" if bigger_side == "LONG" else "LONG"

        filters_failed = []

        if last_exit_time > 0 and (ts - last_exit_time) < cooldown_sec:
            gap_min = (ts - last_exit_time) / 60
            filters_failed.append(f"cooldown ({gap_min:.0f}m < {params.cooldown_min}m)")

        if entry_hour in params.skip_hours:
            filters_failed.append(f"skip_hour ({entry_hour} UTC)")

        if oi_pct > params.oi_gate_pct:
            filters_failed.append(f"oi_gate ({oi_pct:.2f}% > {params.oi_gate_pct:.2f}%)")

        if imb < params.min_imbalance:
            filters_failed.append(f"imbalance ({imb:.2f}x < {params.min_imbalance:.1f}x)")

        ha_color = b.get("ha_color")
        ha_body = b.get("ha_body_ratio")
        ha_streak_val = b.get("ha_streak")
        ha_available = ha_color is not None and ha_color != ""

        if ha_available:
            aligned = (direction == "SHORT" and ha_color == "RED") or \
                      (direction == "LONG" and ha_color == "GREEN")
            if not aligned:
                filters_failed.append(f"ha_color ({ha_color} vs {direction})")
            if ha_streak_val is not None and ha_streak_val < params.ha_min_streak:
                filters_failed.append(f"ha_streak ({ha_streak_val} < {params.ha_min_streak})")
            if ha_body is not None and ha_body < params.ha_min_body_ratio:
                filters_failed.append(f"ha_body ({ha_body:.2f} < {params.ha_min_body_ratio:.1f})")

        passed = len(filters_failed) == 0
        pnl = 0.0
        exit_reason = ""

        if passed:
            notional = balance * params.leverage
            if correct:
                trade_move = abs(move_pct)
            else:
                trade_move = -abs(move_pct)
            fee = notional * (params.maker_fee_pct + params.taker_fee_pct) / 100
            pnl = trade_move / 100 * notional - fee
            balance += pnl
            last_exit_time = ts + duration
            exit_reason = "battle_resolved"

        trades.append({
            "timestamp": ts,
            "entry_time_str": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M"),
            "direction": direction,
            "bigger_side": bigger_side,
            "entry_price": b.get("snapshot_price", 0),
            "exit_price": b.get("resolved_price", 0),
            "move_pct": round(move_pct, 4),
            "oi_change_pct": round(oi_pct, 4),
            "imbalance_ratio": round(imb, 2),
            "hypothesis_correct": bool(correct),
            "passed": passed,
            "filters_failed": filters_failed,
            "pnl": round(pnl, 2),
            "balance_after": round(balance, 2),
            "duration_sec": round(duration, 0),
            "exit_reason": exit_reason,
            "ha_color": ha_color,
            "ha_body_ratio": ha_body,
            "ha_streak": ha_streak_val,
        })

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
            "max_drawdown": round(_calc_max_dd(kept, params.starting_balance), 2),
        },
    }


def _calc_max_dd(trades: list[dict], starting_balance: float) -> float:
    if not trades:
        return 0.0
    peak = starting_balance
    max_dd = 0.0
    for t in trades:
        bal = t["balance_after"]
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return max_dd


# --- Routes ---

@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/sync")
async def sync():
    result = await sync_db()
    return {"status": "synced", **result}


@app.get("/api/battles")
async def get_battles():
    battles = await _load_battles()
    return {"battles": battles, "count": len(battles)}


@app.get("/api/candles")
async def get_candles():
    ohlc, oi = await _load_candles()
    return {"candles": ohlc, "oi": oi}


@app.post("/api/simulate")
async def simulate_endpoint(params: FilterParams):
    battles = await _load_battles()
    return simulate(battles, params)


# --- Startup ---

@app.on_event("startup")
async def startup():
    try:
        await sync_db()
        print("VPS DB synced successfully.")
    except Exception as e:
        print(f"Warning: VPS sync failed on startup: {e}")
        print("Using cached DB if available.")


# --- Static files (mount after routes so / works) ---

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main():
    uvicorn.run("src.visualizer.app:app", host="0.0.0.0", port=8099, reload=True)


if __name__ == "__main__":
    main()
