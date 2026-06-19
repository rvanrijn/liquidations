"""Fetch Bybit BTCUSDT 1h klines and resample to 8H bars (UTC-origin), with pickle cache."""
from __future__ import annotations
import time
from pathlib import Path
import httpx
import pandas as pd

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache"

def resample_to_8h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample a tz-aware 1h OHLCV DataFrame to 8H bars aligned to 00:00 UTC.

    Buckets: [00:00, 08:00, 16:00). A bucket is labelled by its left (opening) edge.
    Drops the final bucket if it is incomplete (fewer than 8 source bars) so the
    backtest never reads a still-forming bar.
    """
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df_1h.resample("8h", origin="epoch", label="left", closed="left").agg(agg)
    counts = df_1h.resample("8h", origin="epoch", label="left", closed="left").size()
    out = out[counts == 8].dropna()
    return out

def _fetch_1h_bybit(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Page Bybit v5 linear klines (interval=60) backward from end_ms to start_ms."""
    rows: list[list] = []
    cursor_end = end_ms
    with httpx.Client(timeout=30) as client:
        while cursor_end > start_ms:
            r = client.get(BYBIT_KLINE_URL, params={
                "category": "linear", "symbol": symbol, "interval": "60",
                "end": cursor_end, "limit": 1000,
            })
            r.raise_for_status()
            batch = r.json()["result"]["list"]  # newest-first: [start, o, h, l, c, vol, turnover]
            if not batch:
                break
            rows.extend(batch)
            oldest = int(batch[-1][0])
            if oldest <= start_ms or len(batch) < 1000:
                break
            cursor_end = oldest - 1
            time.sleep(0.1)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df = df.astype({"ts": "int64", "open": float, "high": float, "low": float, "close": float, "volume": float})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df[["open", "high", "low", "close", "volume"]]

def load_btc_8h(years: float = 3.0, symbol: str = "BTCUSDT", refresh: bool = False) -> pd.DataFrame:
    """Return ~`years` of 8H BTCUSDT bars, cached to pickle under data_cache/."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"{symbol}_8h_{years}y.pkl"
    if cache.exists() and not refresh:
        return pd.read_pickle(cache)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(years * 365 * 24 * 60 * 60 * 1000)
    df_1h = _fetch_1h_bybit(symbol, start_ms, end_ms)
    df_8h = resample_to_8h(df_1h)
    df_8h.to_pickle(cache)
    return df_8h
