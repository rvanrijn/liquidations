# MCB (VuManChu Cipher B) 8H Heikin-Ashi Backtest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an honest, reproducible Python backtest of the VuManChu Cipher B clone on ~3yr Bybit BTCUSDT 8H Heikin-Ashi data, signals read on the closed HA bar and filled at the next real open, and emit a report with a clear edge verdict.

**Architecture:** A small self-contained package `MCBstrat/mcbstrat/` with one responsibility per module (data → heikin_ashi → indicators → signals → backtest → metrics → report), plus a manual `tv_crosscheck` script. Pure functions over pandas DataFrames; an event-loop backtester that confirms signals on closed bar `t` and fills at the real (non-HA) open of bar `t+1`. TDD throughout, frequent commits.

**Tech Stack:** Python 3.11, pandas 3.0, numpy 2.4, httpx (Bybit public REST), pytest. Reuses the Heikin-Ashi pattern from `src/habot/candles.py`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-19-mcb-cipherb-8h-ha-backtest-design.md`

---

## Conventions for the implementer (read first)

- All paths are relative to the repo root `/Users/rvanrijn/Developer/test/liquidations`.
- Python interpreter: `.venv/bin/python`. Run tests with `.venv/bin/python -m pytest`.
- The package lives at `MCBstrat/mcbstrat/`; tests at `MCBstrat/tests/`. A `MCBstrat/pytest.ini` sets the rootdir and adds `MCBstrat` to the path so imports are `from mcbstrat.X import ...`.
- Run pytest as: `cd MCBstrat && ../.venv/bin/python -m pytest -v` (the `pytest.ini` `pythonpath` makes `mcbstrat` importable).
- **Pine→pandas equivalences (use these exactly):**
  - Pine `ema(x, n)` == `x.ewm(span=n, adjust=False).mean()`
  - Pine `sma(x, n)` == `x.rolling(n).mean()`
  - Pine `cross(a, b)` true on bar where `sign(a-b)` flips vs previous bar.
- **No-lookahead is sacred:** a signal decided on bar `t` may only use data up to and including bar `t`; its fill price is bar `t+1`'s real `open`.
- Commit after every task (the final step of each task).

---

## Task 0: Scaffold the package

**Files:**
- Create: `MCBstrat/mcbstrat/__init__.py` (empty)
- Create: `MCBstrat/tests/__init__.py` (empty)
- Create: `MCBstrat/pytest.ini`
- Create: `MCBstrat/.gitignore`

- [ ] **Step 1: Create directories and files**

`MCBstrat/pytest.ini`:
```ini
[pytest]
pythonpath = .
testpaths = tests
python_files = test_*.py
```

`MCBstrat/.gitignore`:
```
__pycache__/
*.pyc
data_cache/
reports/
```

- [ ] **Step 2: Verify pytest collects nothing yet (clean baseline)**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest -v`
Expected: `no tests ran` (exit code 5), no import/config errors.

- [ ] **Step 3: Commit**

```bash
git add MCBstrat/mcbstrat MCBstrat/tests MCBstrat/pytest.ini MCBstrat/.gitignore
git commit -m "chore(mcbstrat): scaffold package + pytest config"
```

---

## Task 1: Data — fetch Bybit BTCUSDT and resample to 8H

Bybit linear perps have no native 8H interval, so fetch native 60-minute klines from Bybit's public v5 REST (`/v5/market/kline`, `category=linear`, `symbol=BTCUSDT`, `interval=60`), paginate backward ~3 years, then resample to 8H bars aligned to 00:00 UTC. Cache to parquet.

**Files:**
- Create: `MCBstrat/mcbstrat/data.py`
- Test: `MCBstrat/tests/test_data.py`

- [ ] **Step 1: Write the failing test for resampling (pure, no network)**

```python
# MCBstrat/tests/test_data.py
import pandas as pd
from mcbstrat.data import resample_to_8h

def test_resample_to_8h_aligns_to_utc_origin_and_aggregates_ohlcv():
    # 16 hourly bars starting 2024-01-01 00:00 UTC -> two full 8H buckets
    idx = pd.date_range("2024-01-01 00:00", periods=16, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open":   range(100, 116),
        "high":   [v + 5 for v in range(100, 116)],
        "low":    [v - 5 for v in range(100, 116)],
        "close":  [v + 1 for v in range(100, 116)],
        "volume": [1.0] * 16,
    }, index=idx)

    out = resample_to_8h(df)

    # buckets: 00:00-07:59 and 08:00-15:59
    assert list(out.index) == [
        pd.Timestamp("2024-01-01 00:00", tz="UTC"),
        pd.Timestamp("2024-01-01 08:00", tz="UTC"),
    ]
    assert out.loc["2024-01-01 00:00", "open"] == 100          # first open of bucket
    assert out.loc["2024-01-01 00:00", "close"] == 108         # last close (108 = 107+1)
    assert out.loc["2024-01-01 00:00", "high"] == 112          # max high (107+5)
    assert out.loc["2024-01-01 00:00", "low"] == 95            # min low (100-5)
    assert out.loc["2024-01-01 00:00", "volume"] == 8.0        # summed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_data.py -v`
Expected: FAIL — `ImportError: cannot import name 'resample_to_8h'`.

- [ ] **Step 3: Implement `resample_to_8h` and the fetcher**

```python
# MCBstrat/mcbstrat/data.py
"""Fetch Bybit BTCUSDT 1h klines and resample to 8H bars (UTC-origin), with parquet cache."""
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
            time.sleep(0.1)  # be polite to the public endpoint
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df = df.astype({"ts": "int64", "open": float, "high": float, "low": float, "close": float, "volume": float})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df[["open", "high", "low", "close", "volume"]]

def load_btc_8h(years: float = 3.0, symbol: str = "BTCUSDT", refresh: bool = False) -> pd.DataFrame:
    """Return ~`years` of 8H BTCUSDT bars, cached to parquet under data_cache/."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"{symbol}_8h_{years}y.parquet"
    if cache.exists() and not refresh:
        return pd.read_parquet(cache)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(years * 365 * 24 * 60 * 60 * 1000)
    df_1h = _fetch_1h_bybit(symbol, start_ms, end_ms)
    df_8h = resample_to_8h(df_1h)
    df_8h.to_parquet(cache)
    return df_8h
```

- [ ] **Step 4: Run the resample test to verify it passes**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_data.py -v`
Expected: PASS.

- [ ] **Step 5: Smoke-test the live fetch once (manual, network)**

Run: `cd MCBstrat && ../.venv/bin/python -c "from mcbstrat.data import load_btc_8h; d=load_btc_8h(); print(d.shape); print(d.head(2)); print(d.tail(2))"`
Expected: ~3200+ rows (3yr × ~1095 8H bars), index at 00:00/08:00/16:00 UTC, sane BTC prices. The parquet cache now exists.

- [ ] **Step 6: Commit**

```bash
git add MCBstrat/mcbstrat/data.py MCBstrat/tests/test_data.py
git commit -m "feat(mcbstrat): Bybit 1h fetch + 8H UTC-origin resample with parquet cache"
```

---

## Task 2: Heikin Ashi (preserve raw OHLC)

Follow the pattern in `src/habot/candles.py:compute_ha` — keep BOTH the HA OHLC (for signals) and the raw OHLC (for fills) on every bar.

**Files:**
- Create: `MCBstrat/mcbstrat/heikin_ashi.py`
- Test: `MCBstrat/tests/test_heikin_ashi.py`

- [ ] **Step 1: Write the failing test (hand-computed first two bars)**

```python
# MCBstrat/tests/test_heikin_ashi.py
import pandas as pd
from mcbstrat.heikin_ashi import add_heikin_ashi

def test_heikin_ashi_first_two_bars_hand_computed():
    idx = pd.date_range("2024-01-01", periods=2, freq="8h", tz="UTC")
    df = pd.DataFrame({
        "open":  [100.0, 110.0],
        "high":  [120.0, 130.0],
        "low":   [ 90.0, 100.0],
        "close": [110.0, 120.0],
        "volume":[1.0, 1.0],
    }, index=idx)

    out = add_heikin_ashi(df)

    # Bar 0: ha_close=(100+120+90+110)/4=105 ; ha_open seed=(open+close)/2=(100+110)/2=105
    assert out.iloc[0]["ha_close"] == 105.0
    assert out.iloc[0]["ha_open"] == 105.0
    # Bar 1: ha_close=(110+130+100+120)/4=115 ; ha_open=(prev ha_open+prev ha_close)/2=(105+105)/2=105
    assert out.iloc[1]["ha_close"] == 115.0
    assert out.iloc[1]["ha_open"] == 105.0
    assert out.iloc[1]["ha_high"] == max(130.0, 105.0, 115.0)
    assert out.iloc[1]["ha_low"] == min(100.0, 105.0, 115.0)
    # raw columns preserved untouched
    assert out.iloc[1]["open"] == 110.0
    assert out.iloc[1]["close"] == 120.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_heikin_ashi.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Implement**

```python
# MCBstrat/mcbstrat/heikin_ashi.py
"""Add Heikin Ashi columns while preserving raw OHLC (raw open used for fills)."""
import pandas as pd

def add_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with ha_open/ha_high/ha_low/ha_close/ha_color added.

    Raw open/high/low/close/volume are left untouched. HA open seeds from
    (open+close)/2 on the first bar, then recurses (prev ha_open + prev ha_close)/2.
    """
    out = df.copy()
    ha_close = (out["open"] + out["high"] + out["low"] + out["close"]) / 4.0
    ha_open = [(out["open"].iloc[0] + out["close"].iloc[0]) / 2.0]
    for i in range(1, len(out)):
        ha_open.append((ha_open[i - 1] + ha_close.iloc[i - 1]) / 2.0)
    out["ha_open"] = ha_open
    out["ha_close"] = ha_close
    out["ha_high"] = pd.concat([out["high"], out["ha_open"], out["ha_close"]], axis=1).max(axis=1)
    out["ha_low"] = pd.concat([out["low"], out["ha_open"], out["ha_close"]], axis=1).min(axis=1)
    out["ha_color"] = (out["ha_close"] >= out["ha_open"]).map({True: "GREEN", False: "RED"})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_heikin_ashi.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add MCBstrat/mcbstrat/heikin_ashi.py MCBstrat/tests/test_heikin_ashi.py
git commit -m "feat(mcbstrat): heikin ashi with preserved raw OHLC"
```

---

## Task 3: WaveTrend indicator

VuManChu WaveTrend on HA `hlc3`. Channel length 9, average length 12, MA length 3 (defaults).

**Files:**
- Create: `MCBstrat/mcbstrat/indicators.py`
- Test: `MCBstrat/tests/test_indicators.py`

- [ ] **Step 1: Write the failing test (independent pandas reference on a synthetic series)**

```python
# MCBstrat/tests/test_indicators.py
import numpy as np
import pandas as pd
from mcbstrat.indicators import wavetrend

def _reference_wavetrend(ha_hlc3, n1=9, n2=12):
    esa = ha_hlc3.ewm(span=n1, adjust=False).mean()
    d = (ha_hlc3 - esa).abs().ewm(span=n1, adjust=False).mean()
    ci = (ha_hlc3 - esa) / (0.015 * d)
    wt1 = ci.ewm(span=n2, adjust=False).mean()
    wt2 = wt1.rolling(3).mean()
    return wt1, wt2

def test_wavetrend_matches_independent_reference():
    rng = np.random.default_rng(42)
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="8h", tz="UTC")
    # build a plausible HA hlc3 series
    ha_hlc3 = pd.Series(30000 + np.cumsum(rng.normal(0, 50, n)), index=idx)

    wt1, wt2 = wavetrend(ha_hlc3)
    ref1, ref2 = _reference_wavetrend(ha_hlc3)

    pd.testing.assert_series_equal(wt1.dropna(), ref1.dropna(), check_names=False)
    pd.testing.assert_series_equal(wt2.dropna(), ref2.dropna(), check_names=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_indicators.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Implement `wavetrend` (and `hlc3` helper)**

```python
# MCBstrat/mcbstrat/indicators.py
"""VuManChu Cipher B components: WaveTrend, Money Flow, and dot detection."""
import numpy as np
import pandas as pd

WT_CHANNEL_LEN = 9
WT_AVERAGE_LEN = 12
WT_MA_LEN = 3
OB_LEVEL = 53.0
OS_LEVEL = -53.0
MFI_PERIOD = 60
MFI_MULTIPLIER = 150.0

def ha_hlc3(df: pd.DataFrame) -> pd.Series:
    return (df["ha_high"] + df["ha_low"] + df["ha_close"]) / 3.0

def wavetrend(src: pd.Series, n1: int = WT_CHANNEL_LEN, n2: int = WT_AVERAGE_LEN, ma: int = WT_MA_LEN):
    """Return (wt1, wt2) WaveTrend lines. Pine ema==ewm(adjust=False); sma==rolling.mean."""
    esa = src.ewm(span=n1, adjust=False).mean()
    d = (src - esa).abs().ewm(span=n1, adjust=False).mean()
    ci = (src - esa) / (0.015 * d)
    wt1 = ci.ewm(span=n2, adjust=False).mean()
    wt2 = wt1.rolling(ma).mean()
    return wt1, wt2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_indicators.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add MCBstrat/mcbstrat/indicators.py MCBstrat/tests/test_indicators.py
git commit -m "feat(mcbstrat): WaveTrend (VuManChu wt1/wt2)"
```

---

## Task 4: Money Flow ("volume" green/red)

VuManChu MFI+RSI area: `sma(((close-open)/(high-low)) * 150, 60)` on **HA** candles. Green = MF > 0, Red = MF < 0.

**Files:**
- Modify: `MCBstrat/mcbstrat/indicators.py`
- Modify: `MCBstrat/tests/test_indicators.py`

- [ ] **Step 1: Write the failing test (sign correctness)**

```python
# append to MCBstrat/tests/test_indicators.py
from mcbstrat.indicators import money_flow

def test_money_flow_sign_follows_ha_body_direction():
    n = 80
    idx = pd.date_range("2024-01-01", periods=n, freq="8h", tz="UTC")
    # all strongly bullish HA bars (close>open) -> MF should be > 0 once warm
    df = pd.DataFrame({
        "ha_open":  [100.0] * n,
        "ha_high":  [110.0] * n,
        "ha_low":   [ 99.0] * n,
        "ha_close": [109.0] * n,
    }, index=idx)
    mf = money_flow(df)
    assert mf.iloc[-1] > 0
    # flip to bearish bodies -> MF must be able to go negative
    df2 = df.copy()
    df2["ha_open"], df2["ha_close"] = 109.0, 100.0
    mf2 = money_flow(df2)
    assert mf2.iloc[-1] < 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_indicators.py::test_money_flow_sign_follows_ha_body_direction -v`
Expected: FAIL — import error.

- [ ] **Step 3: Implement `money_flow`**

```python
# append to MCBstrat/mcbstrat/indicators.py
def money_flow(df: pd.DataFrame, period: int = MFI_PERIOD, mult: float = MFI_MULTIPLIER) -> pd.Series:
    """VuManChu MFI+RSI area on HA candles. >0 green, <0 red.

    sma(((ha_close - ha_open) / (ha_high - ha_low)) * mult, period).
    The RSI/Y-offset constants in VuManChu are display-only and do not change the sign.
    """
    rng = (df["ha_high"] - df["ha_low"]).replace(0, np.nan)
    raw = ((df["ha_close"] - df["ha_open"]) / rng) * mult
    return raw.rolling(period).mean()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_indicators.py -v`
Expected: PASS (all indicator tests).

- [ ] **Step 5: Commit**

```bash
git add MCBstrat/mcbstrat/indicators.py MCBstrat/tests/test_indicators.py
git commit -m "feat(mcbstrat): money flow (VuManChu MFI area) green/red"
```

---

## Task 5: Green/red dot detection

Green dot = wt1 crosses ABOVE wt2 while `wt2 <= -53`. Red dot = wt1 crosses BELOW wt2 while `wt2 >= 53`.

**Files:**
- Modify: `MCBstrat/mcbstrat/indicators.py`
- Modify: `MCBstrat/tests/test_indicators.py`

- [ ] **Step 1: Write the failing test (constructed cross at OB/OS)**

```python
# append to MCBstrat/tests/test_indicators.py
from mcbstrat.indicators import detect_dots

def test_detect_dots_green_on_oversold_crossup_red_on_overbought_crossdown():
    idx = pd.date_range("2024-01-01", periods=4, freq="8h", tz="UTC")
    # bar0: wt1 below wt2 in oversold; bar1: wt1 crosses ABOVE wt2 still oversold -> GREEN
    # bar1: prev wt1(-70) <= prev wt2(-55) AND now wt1(-56) > wt2(-58) => cross up ; wt2(-58) <= -53
    wt1 = pd.Series([-70, -56, 20, 70], index=idx, dtype=float)
    wt2 = pd.Series([-55, -58, 30, 55], index=idx, dtype=float)  # wt2[1]=-58<=-53 ; wt2[3]=55>=53
    green, red = detect_dots(wt1, wt2)
    assert bool(green.iloc[1]) is True       # cross up at oversold
    assert bool(red.iloc[1]) is False
    # bar3->? construct an overbought cross down
    wt1b = pd.Series([10, 70, 65, 40], index=idx, dtype=float)
    wt2b = pd.Series([20, 60, 66, 55], index=idx, dtype=float)  # cross down at bar2 (wt2=60>=53)
    g2, r2 = detect_dots(wt1b, wt2b)
    assert bool(r2.iloc[2]) is True          # wt1 65 < wt2 66 after being above -> cross down, wt2>=53
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_indicators.py::test_detect_dots_green_on_oversold_crossup_red_on_overbought_crossdown -v`
Expected: FAIL — import error.

- [ ] **Step 3: Implement `detect_dots`**

```python
# append to MCBstrat/mcbstrat/indicators.py
def detect_dots(wt1: pd.Series, wt2: pd.Series, ob: float = OB_LEVEL, os_: float = OS_LEVEL):
    """Return (green, red) boolean Series.

    green: wt1 crosses ABOVE wt2 (prev wt1<=wt2, now wt1>wt2) AND wt2 <= os_.
    red:   wt1 crosses BELOW wt2 (prev wt1>=wt2, now wt1<wt2) AND wt2 >= ob.
    """
    prev1, prev2 = wt1.shift(1), wt2.shift(1)
    cross_up = (prev1 <= prev2) & (wt1 > wt2)
    cross_down = (prev1 >= prev2) & (wt1 < wt2)
    green = (cross_up & (wt2 <= os_)).fillna(False)
    red = (cross_down & (wt2 >= ob)).fillna(False)
    return green, red
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_indicators.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add MCBstrat/mcbstrat/indicators.py MCBstrat/tests/test_indicators.py
git commit -m "feat(mcbstrat): green/red dot detection at OB/OS crosses"
```

---

## Task 6: Signal state machine (flat-between)

Produce a per-bar `position` series in {0, +1, -1} and discrete entry/exit events, decided on the **closed** bar. Long when `green & MF>0`; exit long when `MF<0`. Short when `red & MF<0`; exit short when `MF>0`. Never both sides; a long exit does not auto-open a short.

**Files:**
- Create: `MCBstrat/mcbstrat/signals.py`
- Test: `MCBstrat/tests/test_signals.py`

- [ ] **Step 1: Write the failing test (full lifecycle + mutual exclusion)**

```python
# MCBstrat/tests/test_signals.py
import pandas as pd
from mcbstrat.signals import build_signals

def _frame(green, red, mf):
    idx = pd.date_range("2024-01-01", periods=len(green), freq="8h", tz="UTC")
    return pd.DataFrame({"green": green, "red": red, "mf": mf}, index=idx)

def test_long_entry_then_exit_on_mf_red():
    # bar0 green+mf>0 -> enter long; bars1-2 hold; bar3 mf<0 -> exit; bar4 flat
    df = _frame(
        green=[True, False, False, False, False],
        red=  [False, False, False, False, False],
        mf=   [ 10.0,  5.0,   2.0,  -1.0,  -3.0],
    )
    sig = build_signals(df)
    assert list(sig["position"]) == [1, 1, 1, 0, 0]
    assert list(sig["event"]) == ["ENTER_LONG", "", "", "EXIT_LONG", ""]

def test_long_exit_does_not_auto_open_short():
    df = _frame(green=[True, False], red=[False, False], mf=[10.0, -5.0])
    sig = build_signals(df)
    assert list(sig["position"]) == [1, 0]  # mf flipping red exits long, no short (needs red dot)

def test_short_entry_requires_red_dot_and_mf_red():
    df = _frame(green=[False, False, False], red=[True, False, False], mf=[-5.0, -2.0, 3.0])
    sig = build_signals(df)
    assert list(sig["position"]) == [-1, -1, 0]
    assert list(sig["event"]) == ["ENTER_SHORT", "", "EXIT_SHORT"]

def test_no_entry_when_dot_and_mf_disagree():
    # green dot but mf still red -> no long (the expected-sparse case)
    df = _frame(green=[True, True], red=[False, False], mf=[-5.0, -1.0])
    sig = build_signals(df)
    assert list(sig["position"]) == [0, 0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_signals.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Implement `build_signals`**

```python
# MCBstrat/mcbstrat/signals.py
"""Flat-between position state machine over green/red dots and money-flow sign."""
import pandas as pd

def build_signals(df: pd.DataFrame) -> pd.DataFrame:
    """df needs columns: green (bool), red (bool), mf (float).

    Returns df + 'position' (int in {-1,0,1}, the state AS OF this closed bar) and
    'event' (str: ENTER_LONG/EXIT_LONG/ENTER_SHORT/EXIT_SHORT/'').
    """
    positions, events = [], []
    pos = 0
    for green, red, mf in zip(df["green"], df["red"], df["mf"]):
        event = ""
        if pos == 0:
            if green and mf > 0:
                pos, event = 1, "ENTER_LONG"
            elif red and mf < 0:
                pos, event = -1, "ENTER_SHORT"
        elif pos == 1:
            if mf < 0:
                pos, event = 0, "EXIT_LONG"
        elif pos == -1:
            if mf > 0:
                pos, event = 0, "EXIT_SHORT"
        positions.append(pos)
        events.append(event)
    out = df.copy()
    out["position"] = positions
    out["event"] = events
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_signals.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add MCBstrat/mcbstrat/signals.py MCBstrat/tests/test_signals.py
git commit -m "feat(mcbstrat): flat-between signal state machine"
```

---

## Task 7: Backtest engine (next-real-open fills + costs + no-lookahead guard)

Walk the bars. An event decided on bar `t` fills at bar `t+1`'s **raw `open`**. Apply 0.05% slippage to the fill price (adverse) and 0.10% fees per round-trip (0.05% per side). Record one `Trade` per round-trip with return%, MAE%, MFE%, bars held. The last open position is closed at the final bar's raw close for accounting.

**Files:**
- Create: `MCBstrat/mcbstrat/backtest.py`
- Test: `MCBstrat/tests/test_backtest.py`

- [ ] **Step 1: Write the failing tests (fills, costs, and the critical no-lookahead guard)**

```python
# MCBstrat/tests/test_backtest.py
import pandas as pd
from mcbstrat.backtest import run_backtest, CostModel

def _bars(opens, closes, events):
    n = len(opens)
    idx = pd.date_range("2024-01-01", periods=n, freq="8h", tz="UTC")
    return pd.DataFrame({
        "open": opens,
        "high": [max(o, c) + 1 for o, c in zip(opens, closes)],
        "low":  [min(o, c) - 1 for o, c in zip(opens, closes)],
        "close": closes,
        "position": [ {"ENTER_LONG":1,"EXIT_LONG":0,"ENTER_SHORT":-1,"EXIT_SHORT":0}.get(e, None) for e in events ],
        "event": events,
    }, index=idx)

def test_long_trade_fills_at_next_real_open_with_costs():
    # ENTER_LONG on bar0 -> fill at bar1 open (100). EXIT_LONG on bar2 -> fill at bar3 open (110).
    df = _bars(
        opens=[ 50, 100, 105, 110],
        closes=[60,  101, 106, 111],
        events=["ENTER_LONG", "", "EXIT_LONG", ""],
    )
    trades, _ = run_backtest(df, CostModel(fee_side_pct=0.05, slippage_pct=0.05))
    assert len(trades) == 1
    t = trades[0]
    assert t.side == "LONG"
    assert round(t.entry_price, 4) == round(100 * (1 + 0.0005), 4)  # slippage adverse on buy
    assert round(t.exit_price, 4) == round(110 * (1 - 0.0005), 4)   # slippage adverse on sell
    # gross ~ +10%, minus ~0.10% fees + ~0.10% slippage
    assert 0.094 < t.return_pct < 0.099

def test_no_lookahead_entry_uses_next_bar_open_not_signal_bar():
    # If the engine cheated and used bar0's own open (50) as the entry, return would differ wildly.
    df = _bars(opens=[50, 100, 110], closes=[60, 101, 111], events=["ENTER_LONG", "", "EXIT_LONG"])
    trades, _ = run_backtest(df, CostModel(0.0, 0.0))
    assert len(trades) == 1
    assert trades[0].entry_price == 100.0   # bar1 open, NOT bar0 open(50) and NOT bar0 close(60)
    assert trades[0].exit_price == 110.0    # bar2 open

def test_open_position_closed_at_final_bar_close():
    df = _bars(opens=[50, 100, 105], closes=[60, 101, 130], events=["ENTER_LONG", "", ""])
    trades, _ = run_backtest(df, CostModel(0.0, 0.0))
    assert len(trades) == 1
    assert trades[0].exit_price == 130.0    # forced close at last raw close
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_backtest.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Implement the engine**

```python
# MCBstrat/mcbstrat/backtest.py
"""Event-loop backtest: signal on closed bar t, fill at bar t+1 raw open."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

@dataclass
class CostModel:
    fee_side_pct: float = 0.05      # per side, percent (0.05 == 0.05%)
    slippage_pct: float = 0.05      # per fill, percent, applied adverse to direction

@dataclass
class Trade:
    side: str                       # "LONG" or "SHORT"
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    return_pct: float               # net of fees+slippage, as a fraction (0.05 == +5%)
    mae_pct: float
    mfe_pct: float
    bars_held: int

def _apply_slip(price: float, slip: float, is_buy: bool) -> float:
    return price * (1 + slip / 100.0) if is_buy else price * (1 - slip / 100.0)

def run_backtest(df: pd.DataFrame, costs: CostModel):
    """df needs raw open/high/low/close and an 'event' column. Returns (trades, equity_series)."""
    trades: list[Trade] = []
    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    times = df.index
    events = df["event"].to_numpy()
    n = len(df)

    in_pos = False
    side = None
    entry_price = 0.0
    entry_i = 0
    fee = costs.fee_side_pct / 100.0

    def open_trade(i_fill, s):
        nonlocal in_pos, side, entry_price, entry_i
        is_buy = (s == "LONG")
        entry_price = _apply_slip(opens[i_fill], costs.slippage_pct, is_buy)
        side, entry_i, in_pos = s, i_fill, True

    def close_trade(i_fill, exit_px_raw, exit_time):
        nonlocal in_pos
        is_buy_to_close = (side == "SHORT")
        exit_price = _apply_slip(exit_px_raw, costs.slippage_pct, is_buy_to_close)
        if side == "LONG":
            gross = (exit_price - entry_price) / entry_price
            mae = (lows[entry_i:i_fill + 1].min() - entry_price) / entry_price
            mfe = (highs[entry_i:i_fill + 1].max() - entry_price) / entry_price
        else:
            gross = (entry_price - exit_price) / entry_price
            mae = (entry_price - highs[entry_i:i_fill + 1].max()) / entry_price
            mfe = (entry_price - lows[entry_i:i_fill + 1].min()) / entry_price
        net = gross - 2 * fee
        trades.append(Trade(side, times[entry_i], exit_time, entry_price, exit_price,
                            net, mae, mfe, i_fill - entry_i))
        in_pos = False

    for i in range(n):
        ev = events[i]
        nxt = i + 1
        if not in_pos and ev in ("ENTER_LONG", "ENTER_SHORT") and nxt < n:
            open_trade(nxt, "LONG" if ev == "ENTER_LONG" else "SHORT")
        elif in_pos and ev in ("EXIT_LONG", "EXIT_SHORT") and nxt < n:
            close_trade(nxt, opens[nxt], times[nxt])
    if in_pos:
        close_trade(n - 1, closes[n - 1], times[n - 1])

    # simple compounded equity from sequential trade returns
    eq = [1.0]
    for t in trades:
        eq.append(eq[-1] * (1 + t.return_pct))
    equity = pd.Series(eq[1:], index=[t.exit_time for t in trades]) if trades else pd.Series(dtype=float)
    return trades, equity
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_backtest.py -v`
Expected: PASS (3 tests, including the no-lookahead guard).

- [ ] **Step 5: Commit**

```bash
git add MCBstrat/mcbstrat/backtest.py MCBstrat/tests/test_backtest.py
git commit -m "feat(mcbstrat): backtest engine with next-open fills, costs, no-lookahead guard"
```

---

## Task 8: Metrics, regime tagging, OOS split, minimum-N gate

**Files:**
- Create: `MCBstrat/mcbstrat/metrics.py`
- Test: `MCBstrat/tests/test_metrics.py`

Fixed regime windows (documented, reproducible — declared once here):
- **BULL:** 2023-01-01 → 2024-03-31
- **BEAR/CHOP:** 2024-04-01 → 2024-10-31
- **BULL2/CHOP:** 2024-11-01 → present
(These are illustrative anchors; the implementer pins exact dates from the loaded data span and records them in the report. The point is fixed, not hand-picked-per-run.)

- [ ] **Step 1: Write the failing test (core stats + minimum-N verdict)**

```python
# MCBstrat/tests/test_metrics.py
import pandas as pd
from mcbstrat.backtest import Trade
from mcbstrat.metrics import summarize, verdict_for_side, MIN_TRADES

def _t(side, ret, t0, t1):
    return Trade(side, pd.Timestamp(t0, tz="UTC"), pd.Timestamp(t1, tz="UTC"), 100, 100*(1+ret), ret, 0.0, 0.0, 1)

def test_summarize_basic_stats():
    trades = [_t("LONG", 0.10, "2024-01-01", "2024-01-02"),
              _t("LONG", -0.05, "2024-02-01", "2024-02-02"),
              _t("LONG", 0.20, "2024-03-01", "2024-03-02")]
    s = summarize(trades)
    assert s["n"] == 3
    assert s["wins"] == 2
    assert round(s["win_rate"], 4) == round(2/3, 4)
    assert s["profit_factor"] > 1
    assert round(s["expectancy"], 4) == round((0.10 - 0.05 + 0.20) / 3, 4)

def test_verdict_insufficient_power_below_min_trades():
    assert verdict_for_side([_t("SHORT", 0.01, "2024-01-01", "2024-01-02")]) == "inconclusive / insufficient power"

def test_verdict_uses_expectancy_when_enough_trades():
    losers = [_t("SHORT", -0.02, f"2024-{m:02d}-01", f"2024-{m:02d}-02") for m in range(1, 13)] * 2
    assert len(losers) >= MIN_TRADES
    assert verdict_for_side(losers) == "negative edge"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Implement `metrics.py`**

```python
# MCBstrat/mcbstrat/metrics.py
"""Aggregate stats, OOS split, regime tagging, minimum-N verdict gate."""
from __future__ import annotations
import pandas as pd

MIN_TRADES = 20

def summarize(trades) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "expectancy": 0.0, "total_return": 0.0, "max_dd": 0.0, "avg_bars": 0.0}
    rets = [t.return_pct for t in trades]
    wins = sum(1 for r in rets if r > 0)
    gross_win = sum(r for r in rets if r > 0)
    gross_loss = -sum(r for r in rets if r < 0)
    eq = 1.0
    curve = []
    for r in rets:
        eq *= (1 + r)
        curve.append(eq)
    peak, max_dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)
    return {
        "n": n, "wins": wins, "win_rate": wins / n,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy": sum(rets) / n,
        "total_return": curve[-1] - 1,
        "max_dd": max_dd,
        "avg_bars": sum(t.bars_held for t in trades) / n,
    }

def verdict_for_side(trades) -> str:
    if len(trades) < MIN_TRADES:
        return "inconclusive / insufficient power"
    exp = sum(t.return_pct for t in trades) / len(trades)
    if exp > 0.002:
        return "positive edge"
    if exp < 0:
        return "negative edge"
    return "marginal"

def split_oos(trades, frac_in: float = 0.70):
    """Chronological split by trade order. Returns (in_sample, out_of_sample)."""
    trades = sorted(trades, key=lambda t: t.entry_time)
    k = int(len(trades) * frac_in)
    return trades[:k], trades[k:]

def tag_regime(ts: pd.Timestamp, windows: list[tuple[str, pd.Timestamp, pd.Timestamp]]) -> str:
    for name, lo, hi in windows:
        if lo <= ts <= hi:
            return name
    return "UNTAGGED"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add MCBstrat/mcbstrat/metrics.py MCBstrat/tests/test_metrics.py
git commit -m "feat(mcbstrat): metrics, OOS split, regime tag, minimum-N verdict gate"
```

---

## Task 9: Report renderer + end-to-end run script

Ties everything together: load data → HA → indicators → signals → backtest → metrics → markdown report (with zero-cost column) under `MCBstrat/reports/`.

**Files:**
- Create: `MCBstrat/mcbstrat/report.py`
- Create: `MCBstrat/mcbstrat/run.py`
- Test: `MCBstrat/tests/test_report.py`

- [ ] **Step 1: Write the failing test (report renders required sections from a summary dict)**

```python
# MCBstrat/tests/test_report.py
from mcbstrat.report import render_markdown

def test_report_contains_required_sections():
    payload = {
        "symbol": "BTCUSDT", "bars": 3200, "span": "2023-01-01 .. 2026-06-19",
        "cost": {"all": {"n": 40, "win_rate": 0.5, "profit_factor": 1.2, "expectancy": 0.01,
                         "total_return": 0.4, "max_dd": -0.2, "avg_bars": 6},
                 "long": {"n": 8}, "short": {"n": 32}},
        "zero_cost": {"all": {"total_return": 0.6}},
        "verdict_long": "inconclusive / insufficient power",
        "verdict_short": "negative edge",
        "oos": {"in": {"expectancy": 0.012}, "out": {"expectancy": 0.004}},
        "regimes": {"BULL": {"n": 20, "expectancy": 0.02}, "BEAR": {"n": 20, "expectancy": -0.01}},
        "tv_crosscheck": "not run",
    }
    md = render_markdown(payload)
    for needle in ["Trade count", "Long", "Short", "Out-of-sample", "Regime",
                   "Zero-cost", "Verdict", "insufficient power"]:
        assert needle in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_report.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Implement `report.py` then `run.py`**

```python
# MCBstrat/mcbstrat/report.py
"""Render the backtest report as markdown."""

def render_markdown(p: dict) -> str:
    c = p["cost"]["all"]
    lines = [
        f"# MCB Cipher B 8H HA Backtest — {p['symbol']}",
        "",
        f"- **Span:** {p['span']}  ·  **Bars:** {p['bars']}",
        f"- **TV cross-check:** {p['tv_crosscheck']}",
        "",
        "## Headline (with costs)",
        f"- **Trade count:** {c['n']}  (Long {p['cost']['long']['n']} / Short {p['cost']['short']['n']})",
        f"- Win rate: {c['win_rate']:.1%}  ·  Profit factor: {c['profit_factor']:.2f}",
        f"- Expectancy/trade: {c['expectancy']:.2%}  ·  Total return: {c['total_return']:.1%}",
        f"- Max drawdown: {c['max_dd']:.1%}  ·  Avg bars held: {c['avg_bars']:.1f}",
        "",
        "## Zero-cost (raw signal edge)",
        f"- Total return: {p['zero_cost']['all']['total_return']:.1%}",
        "",
        "## Long vs Short",
        f"- Long verdict: **{p['verdict_long']}**",
        f"- Short verdict: **{p['verdict_short']}**",
        "",
        "## Out-of-sample (70/30)",
        f"- In-sample expectancy: {p['oos']['in']['expectancy']:.2%}",
        f"- Out-of-sample expectancy: {p['oos']['out']['expectancy']:.2%}",
        "",
        "## Regime breakdown",
    ]
    for name, r in p["regimes"].items():
        lines.append(f"- {name}: n={r['n']}, expectancy {r['expectancy']:.2%}")
    lines += ["", "## Verdict", p.get("final_verdict", "_fill in after reviewing the numbers_"), ""]
    return "\n".join(lines)
```

```python
# MCBstrat/mcbstrat/run.py
"""End-to-end: data -> HA -> indicators -> signals -> backtest -> metrics -> report."""
from pathlib import Path
import pandas as pd
from mcbstrat.data import load_btc_8h
from mcbstrat.heikin_ashi import add_heikin_ashi
from mcbstrat.indicators import ha_hlc3, wavetrend, money_flow, detect_dots
from mcbstrat.signals import build_signals
from mcbstrat.backtest import run_backtest, CostModel
from mcbstrat.metrics import summarize, verdict_for_side, split_oos, tag_regime
from mcbstrat.report import render_markdown

REPORTS = Path(__file__).resolve().parent.parent / "reports"

def _enrich(df):
    df = add_heikin_ashi(df)
    src = ha_hlc3(df)
    wt1, wt2 = wavetrend(src)
    df["mf"] = money_flow(df)
    df["green"], df["red"] = detect_dots(wt1, wt2)
    return build_signals(df[["open", "high", "low", "close", "green", "red", "mf"]])

def main():
    REPORTS.mkdir(exist_ok=True)
    raw = load_btc_8h(years=3.0)
    sig = _enrich(raw)
    cost = CostModel(fee_side_pct=0.05, slippage_pct=0.05)
    trades, _ = run_backtest(sig, cost)
    trades0, _ = run_backtest(sig, CostModel(0.0, 0.0))

    longs = [t for t in trades if t.side == "LONG"]
    shorts = [t for t in trades if t.side == "SHORT"]
    ins, out = split_oos(trades)
    windows = [
        ("BULL_23_24",  pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2024-03-31", tz="UTC")),
        ("CHOP_BEAR_24", pd.Timestamp("2024-04-01", tz="UTC"), pd.Timestamp("2024-10-31", tz="UTC")),
        ("BULL2_25_26", pd.Timestamp("2024-11-01", tz="UTC"), pd.Timestamp("2030-01-01", tz="UTC")),
    ]
    regimes = {}
    for name, lo, hi in windows:
        rt = [t for t in trades if tag_regime(t.entry_time, windows) == name]
        regimes[name] = summarize(rt)

    payload = {
        "symbol": "BTCUSDT", "bars": len(raw),
        "span": f"{raw.index[0].date()} .. {raw.index[-1].date()}",
        "cost": {"all": summarize(trades), "long": summarize(longs), "short": summarize(shorts)},
        "zero_cost": {"all": summarize(trades0)},
        "verdict_long": verdict_for_side(longs),
        "verdict_short": verdict_for_side(shorts),
        "oos": {"in": summarize(ins), "out": summarize(out)},
        "regimes": regimes,
        "tv_crosscheck": "see tv_crosscheck.py output (manual)",
    }
    md = render_markdown(payload)
    (REPORTS / "mcb_cipherb_8h_backtest.md").write_text(md)
    print(md)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the report test to verify it passes**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest tests/test_report.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `cd MCBstrat && ../.venv/bin/python -m pytest -v`
Expected: PASS (all tests across all modules).

- [ ] **Step 6: Commit**

```bash
git add MCBstrat/mcbstrat/report.py MCBstrat/mcbstrat/run.py MCBstrat/tests/test_report.py
git commit -m "feat(mcbstrat): markdown report renderer + end-to-end run script"
```

---

## Task 10: TV cross-check script (manual, not in CI)

Validate the Python reproduction against the live TradingView chart via MCP. This is a manual one-time gate, NOT part of the automated suite (it needs a live TV session).

**Files:**
- Create: `MCBstrat/mcbstrat/tv_crosscheck.py`

- [ ] **Step 1: Implement the cross-check procedure (documented script)**

```python
# MCBstrat/mcbstrat/tv_crosscheck.py
"""MANUAL: compare Python WaveTrend/MoneyFlow sign to TradingView's VuManChu plot.

Run interactively (not under pytest). Procedure the operator/agent follows with the
TradingView MCP tools:

  1. chart_set_symbol BTCUSDT (Bybit) ; chart_set_timeframe 8H ; chart_set_type Heikin Ashi
  2. Add the 'VMC Cipher_B_Divergences' (VuManChu) indicator to the chart.
  3. For ~10 recent CLOSED bars (optionally via replay_step), read:
       - data_get_study_values -> VuManChu wt1, wt2, MFI-area sign
  4. Compute the same bars in Python from load_btc_8h() + indicators, and compare:
       - sign(wt1-wt2) match, green/red dot match, MF sign match.
  5. Record the match rate; paste it into the report's 'TV cross-check' line.

This file intentionally has no automated test: it depends on a live MCP session and is a
validation gate, per the spec (Section 5 note + Section 10 DoD).
"""

def compare(py_rows: list[dict], tv_rows: list[dict]) -> dict:
    """Pure helper: each row dict has keys wt_sign, dot, mf_sign. Returns match rates."""
    assert len(py_rows) == len(tv_rows)
    n = len(py_rows) or 1
    wt = sum(p["wt_sign"] == t["wt_sign"] for p, t in zip(py_rows, tv_rows)) / n
    dot = sum(p["dot"] == t["dot"] for p, t in zip(py_rows, tv_rows)) / n
    mf = sum(p["mf_sign"] == t["mf_sign"] for p, t in zip(py_rows, tv_rows)) / n
    return {"wt_match": wt, "dot_match": dot, "mf_match": mf}
```

- [ ] **Step 2: Quick sanity check the pure helper imports**

Run: `cd MCBstrat && ../.venv/bin/python -c "from mcbstrat.tv_crosscheck import compare; print(compare([{'wt_sign':1,'dot':'G','mf_sign':1}],[{'wt_sign':1,'dot':'G','mf_sign':1}]))"`
Expected: `{'wt_match': 1.0, 'dot_match': 1.0, 'mf_match': 1.0}`

- [ ] **Step 3: Commit**

```bash
git add MCBstrat/mcbstrat/tv_crosscheck.py
git commit -m "feat(mcbstrat): manual TV MCP cross-check helper + procedure"
```

---

## Task 11: Run end-to-end, execute TV cross-check, write verdict

**Files:**
- Modify: `MCBstrat/reports/mcb_cipherb_8h_backtest.md` (generated)
- Create/Modify: report's final verdict section

- [ ] **Step 1: Run the full backtest end-to-end (network: fetches/caches data)**

Run: `cd MCBstrat && ../.venv/bin/python -m mcbstrat.run`
Expected: prints the report and writes `MCBstrat/reports/mcb_cipherb_8h_backtest.md`. Sanity-check: trade count is non-zero; long count is plausibly small (the expected-sparse case); numbers are finite.

- [ ] **Step 2: Execute the TV cross-check (manual, TradingView MCP)**

Follow the procedure in `tv_crosscheck.py` against a live 8H Bybit BTCUSDT Heikin-Ashi chart with the VuManChu indicator. Record wt/dot/mf match rates. If match is poor, STOP and reconcile (likely 8H bar-origin alignment or HA-source mismatch) before trusting results. Paste the match rates into the report's TV cross-check line.

- [ ] **Step 3: Write the final verdict into the report**

Edit `MCBstrat/reports/mcb_cipherb_8h_backtest.md`'s `## Verdict` section: state plainly whether the edge is present/cost-survivable, marginal, or absent — leading with trade count and OOS behavior, per the spec's Definition of Done. Honor the minimum-N gate (a near-zero long sample is "inconclusive," not "absent").

- [ ] **Step 4: Commit**

```bash
git add MCBstrat/reports/mcb_cipherb_8h_backtest.md
git commit -m "report(mcbstrat): 3yr BTCUSDT 8H HA backtest results + verdict + TV cross-check"
```

---

## Definition of Done (from the spec)

- [ ] All tests pass, including the no-lookahead guard (`tests/test_backtest.py`).
- [ ] Backtest runs end-to-end on ~3yr Bybit BTCUSDT 8H and emits the report.
- [ ] TV cross-check executed and its match rate recorded in the report.
- [ ] A clear verdict stated — edge present / marginal / absent — with trade count and OOS behavior front and center, and the minimum-N gate honored.
