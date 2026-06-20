# Liqhunt Cascade Radar — Design

> Live, browser-based cascade radar that taps the liqhunt signal engine's perception in real time and renders it with D3.
> Date: 2026-06-20

---

## 1. Purpose

A striking, single-user **situational-awareness screen** to watch during a trading session. It visualizes, in real time, exactly what the liqhunt/krakenbot signal engine perceives — the live liquidation stream, the magnet liquidation levels, OI/CVD/funding/imbalance, the quality score, and the engine's current verdict (`ARMED` or `STANDBY` with a rejection reason).

It is **read-only and signals-only**: it re-runs the engine's perception locally and never places trades, never touches the production VPS bot, and never writes to any database.

### Non-goals (v1)
- No display of the VPS bot's actual live positions/PnL ("battle overlay"). The locally re-derived signal state is the scope. A battle overlay can be added later via DB sync.
- No order execution, no paper trading, no DB writes.
- No multi-user hosting, auth, or cloud deploy. Single local process opened in a browser.
- No heavy interaction (it is a watch screen).

---

## 2. Context

The `liquidations` repo contains a mature liqhunt/krakenbot system. The production trader runs on a **VPS** (`16.171.155.223`); the existing `src/visualizer/` is a post-hoc tool that SCPs `krakenbot.db` down and renders historical battles — there is **no live push channel today**.

The key enabling fact: the engine's perception is **locally re-derivable in real time** from public Binance feeds, with no auth and no VPS dependency:

- `src/liqhunt/liq_feed.py` — `LiqFeed` streams real-time liquidations from Binance `forceOrder` WebSocket.
- `src/liqhunt/signal_engine.py` — `SignalEngine.evaluate(...)` is effectively a pure function of live market state; it returns a `Signal` or `None` and always sets a human-readable `rejection_reason`.
- `src/liqhunt/main.py` already assembles the full live loop (liq levels/magnet via `BinanceLiqClient` + `MagnetMonitor`, candles via `CandleFetcher`, order flow/CVD via `OrderFlowAggregator` + `BinanceTradeClient`, OI/price/CVD history deques, OI/price warm-up seeding).

The radar's `runner.py` is structurally `liqhunt/main.py` **minus** the three traders (`BattleTrader`/`PaperTrader`/`SigmoidTrader`) and the terminal dashboard, **plus** a shared state object and a WebSocket broadcaster.

---

## 3. Architecture

New module `src/radar/` in the `liquidations` repo, sibling to `src/visualizer/`.

```
src/radar/
├── __init__.py
├── runner.py        # read-only engine loop → maintains RadarState, emits events
├── state.py         # RadarState dataclass + JSON-safe serialization
├── server.py        # FastAPI: serves static/, /ws broadcast, /healthz
└── static/
    ├── index.html
    ├── radar.js     # D3 rendering + WS client
    └── style.css
```

### Data flow

```
Binance public WS/REST ─┐
                        ├─► runner loop ──► RadarState ──► server ──► WS /ws ──► radar.js (D3)
LiqFeed (forceOrder) ───┘        │
                                 └─► per-event push (liquidation blips)
```

### Components

- **`runner.py`** — Owns the live loop, copied structurally from `liqhunt/main.py` but **strictly read-only**: instantiates `BinanceLiqClient` + `MagnetMonitor`, `CandleFetcher`, `LiqFeed`, `OrderFlowAggregator` + `BinanceTradeClient`, the OI/price/CVD history deques, and one `SignalEngine`. Each cycle it calls `signal_engine.evaluate(...)` with the identical arguments the bot uses and writes the result into a shared `RadarState`. Imports **no** trader/executor module and opens **no** DB write handle. Exposes a callback so each `LiqFeed` event is pushed the instant it arrives (not only at snapshot cadence). Applies the same cold-start seeding (5m klines for price history, `openInterestHist` for OI history). Each cycle is wrapped in try/except so one bad cycle logs and continues rather than killing the loop.

  **`evaluate()` itself is pure, but feeding it is not free.** The runner must faithfully reproduce the pre-`evaluate` derived-input math currently inline in `main.py` — this is a runner responsibility, not "just call evaluate," and the plan must budget for porting it (~40 lines):
  - `oi_change_pct` — rolling-peak OI delta over the OI history deque.
  - `oi_velocity` — %/min from the ~12-sample lookback.
  - `cvd_30m` — running sum of 1m CVD deltas over the CVD history deque.
  - `price_range_6h` — maintained from the price history deque.
  - `btc_delta_1m` — from `OrderFlowAggregator.totals()`.
  - `taker_ratio` — via the liq client's taker-ratio call.
- **`state.py`** — `RadarState` dataclass: latest price, magnet levels (long/short, each with USD), OI Δ %, OI velocity, CVD 30m, funding rate, imbalance ratio, taker ratio, quality score, 6h range, signal verdict (`ARMED` | `none`) with direction/target/stop when armed, `rejection_reason`, and a `connections` map (feed health). One method → JSON-safe dict. No logic lives here; it is the serializable contract.
- **`server.py`** — FastAPI app following `src/visualizer/app.py` conventions. Serves `static/`, exposes `GET /healthz` and `WS /ws`. Launches the runner loop as a background task on startup. Maintains a set of connected WS clients; a send failure drops only that client. New clients receive an immediate `state` snapshot on connect.
- **`static/`** — pure view: `index.html`, `radar.js` (D3 v7 + SVG), `style.css`.

### Boundaries / testability rationale
`runner` knows the engine, `state` is the serializable contract, `server` is pure transport, `static` is pure view. Each is testable in isolation: `RadarState` serialization and runner state-building can be exercised with fake feeds, no browser or sockets required.

---

## 4. WebSocket protocol

Two JSON message types over `/ws`, each tagged with `"type"`:

1. **`liq`** — emitted immediately per liquidation event (drives the animated blips):
   ```json
   {"type":"liq","ts":1750000000.0,"side":"long","usd":125000.0,"price":61530.0}
   ```
   `side` semantics: `"long"` = a long was liquidated (Binance `forceOrder` SELL), `"short"` = a short was liquidated (BUY).

2. **`state`** — emitted at a fixed cadence (~2–4 Hz) with the full current picture:
   ```json
   {"type":"state","price":61540.0,"magnets":{"long":[[60950,4.2e6]],"short":[[62380,2.1e6]]},
    "oi_delta_pct":-0.41,"oi_velocity":-0.07,"cvd_30m":-1.2e6,"funding":0.0001,
    "imbalance":1.42,"bigger_side":"LONG","taker_ratio":1.08,"quality":0.62,
    "range_6h":1850.0,"verdict":"none","rejection_reason":"Imbalance insufficient (1.18x < 1.3x)",
    "connections":{"liq_feed":true,"trade_feed":true}}
   ```

On connect the client immediately receives one `state` message so the screen populates without waiting a full cycle.

**Null/empty cases:** the snapshot-derived fields — `magnets`, `imbalance`, `bigger_side` — come from `monitor.snapshot`, which is `None` before the first magnet snapshot. In that state they serialize as empty/null (`magnets: {"long":[], "short":[]}`, `imbalance: null`, `bigger_side: null`), and the frontend hides the bands per §7. The example above shows the fully-warmed payload.

---

## 5. The D3 radar visual

Core metaphor: a **scrolling time × price field** — a heartbeat-monitor where liquidations rain onto a price trace and are pulled toward the magnet levels. Reads left-to-right as "the last ~5 minutes of the cascade."

```
┌──────────────────────────────────────────────────────────────┬───────────────┐
│  BTC  $61,540   ▲       ████ ARMED — STANDBY ████              │ QUALITY       │
│                                                                │   ◔ 0.62      │
│ 62.4k ░░░░░░░░ short-liq magnet  $62,380 ░░░░░░░░░░░░░░ (glow) │ ───────────── │
│            · ·    ∘                          ∘                 │ OI Δ   -0.41% │
│ 62.0k        ·         ·      ∘     ·                          │ OI vel -0.07  │
│      ╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴price trace╴╴╴╴╴●  ← now (centered) │ CVD30 ▇▇▁ -   │
│ 61.5k ══════════════════════════════════════════ price line   │ Funding +0.01 │
│              ◯   ●        ◯        ●   ●  ● ← long-liq blips    │ Imbalance     │
│ 61.0k ▓▓▓▓▓▓▓▓ long-liq magnet $60,950 ▓▓▓▓▓▓▓▓▓▓▓▓ (dominant) │  L ███▌··· S  │
│ 60.6k                                                          │ 1.42x bias L  │
│  ╶──────────────────── time (5 min, scrolls left) ──────────╴ │ Taker 1.08    │
│ cascade intensity ▁▁▂▃▅▇█▆▃▂▁  (net-side colored storm strip)  │ ● liq ● trade │
└──────────────────────────────────────────────────────────────┴───────────────┘
```

### Main field encodings
- **x = time** — last ~5 min, newest at right, scrolls left continuously (rAF loop). Older events age out and fade.
- **y = price** — centered on the live price (price line pinned at vertical center; the world scrolls around it). Auto-scales to always include the nearest magnet on each side.
- **Price line** — a thin trace of recent price; the client accumulates it from `state` ticks into a rolling buffer (backend stays dumb), with a bright "now" dot at the right edge.
- **Liquidation blips** — each `liq` message spawns a circle at `(event_time_x, event_price_y)`; **radius ∝ log(USD)**, **color by side** (long-liq = warm red/orange, short-liq = green/cyan). Pulses (expand + fade) on entry; large events drop a ripple ring. Blips drift left with the field and fade as they exit the window. Clustering of blips against a magnet band *is* the cascade, made visible.
- **Magnet bands** — translucent horizontal zones at the engine's liq-cluster prices, spanning full width. **Opacity/thickness ∝ USD liquidity.** The dominant ("bigger") side glows brighter (SVG `feGaussianBlur`), with a subtle pull-cue as price drifts toward it.
- **Cascade intensity strip** (bottom) — rolling liquidation-$ per 5s bucket as an area chart colored by net side. The visible form of the engine's storm/OI gate.

### Right rail — the engine's brain (the star)
- **Verdict banner** (top, large): `ARMED LONG/SHORT` with target & stop in the accent color when `evaluate()` returns a Signal; otherwise `STANDBY` with the live `rejection_reason` in muted text. The one thing you glance at.
- **Quality score** — radial gauge 0–1.
- **OI Δ % / OI velocity** — bars that turn red as OI drops (cascade tell). OI velocity is in **%/min**; label the unit so it isn't read as a raw delta.
- **CVD 30m** (signed), **funding**, **taker ratio** — compact readouts.
- **Imbalance** — long-vs-short split bar highlighting the bigger side + the ratio (`1.42x bias L`).
- **Connection dots** — Binance liq feed + trade feed health.

### Color & tech
- Dark background (~`#0a0e14`); long-liq = red/orange, short-liq = green/cyan, ARMED = bright accent, neutral = gray. Glow via SVG filters for the radar aesthetic.
- **D3 v7 + SVG.** `requestAnimationFrame` drives the smooth leftward scroll; D3 data-joins manage blips (keyed by event id) and magnet bands (keyed by price). `state` messages refresh gauges/bands/verdict; `liq` messages append blips. The client buffers its own price/intensity history.
- **Interaction is deliberately minimal**: hover a blip → tooltip ($ + price); `space` to freeze/unfreeze. Nothing else (YAGNI).

---

## 6. Error handling & resilience

- **Feed disconnects** — `LiqFeed` and `BinanceTradeClient` already auto-reconnect with backoff; the runner never crashes on a feed drop. Feed live/dead status flows into `RadarState.connections` and renders as the rail's connection dots, so a stale screen is always *visibly* stale rather than silently frozen.
- **Cold start / warm-up** — same seeding the bot uses (5m klines for price history, `openInterestHist` for OI). Until histories fill, the verdict shows `STANDBY — warming up` rather than a misleading rejection reason.
- **WS client churn** — `server.py` tolerates clients connecting/disconnecting mid-broadcast; a send failure drops only that client.
- **Runner exception isolation** — each cycle is wrapped in try/except: one bad cycle (e.g., a malformed REST response) logs and continues. `evaluate()` already returns a `rejection_reason` instead of throwing for "no trade" cases.
- **Read-only guarantee** — the runner imports none of `BattleTrader`/`PaperTrader`/`SigmoidTrader`/executor and opens no DB write handle; asserted by a test so the property cannot silently regress.

---

## 7. Edge cases

- **No magnet snapshot yet** → bands hidden, verdict `STANDBY` with the engine's reason; field still streams liquidations.
- **Liquidation outside the visible price window** → clamped to the top/bottom edge with a small "off-scale" arrow so a far-away cascade is still felt.
- **Burst of events** (real cascade) → live blips capped at a max count (oldest culled) to protect frame rate; the cascade-intensity strip still reflects true totals.
- **Clock skew** — x-axis uses feed event timestamps; the scroll "now" uses the client clock. Small drift is cosmetic and self-corrects each frame.

---

## 8. Testing

pytest, matching the repo's existing style:

- `RadarState` → dict serialization is JSON-safe and stable (unit).
- Runner state-building with **injected fake feeds**: given a scripted `LiqSnapshot` + candles + OI/CVD inputs, the `RadarState` fields and the `evaluate()` verdict come out as expected — no sockets, no network.
- Per-event callback emits a well-formed `liq` message for a synthetic `forceOrder` payload (long-liq vs short-liq side mapping).
- **Read-only assertion**: a test that the `radar` package imports no trader/executor module and performs no DB writes.
- Server: a `TestClient` WS test — connect, receive an initial `state`, receive a pushed `liq`.
- Frontend pure helpers (price→y scale, USD→radius, blip aging) are extracted so they are unit-testable in isolation if a JS test runner is added later. No browser-driver tests in v1 (YAGNI).

---

## 9. Running

```bash
# from the liquidations repo root
uvicorn src.radar.server:app --reload --port 8800
# open http://localhost:8800
```

Single local process, no VPS, no auth, no trades. Mirrors how `src/visualizer` is run.

---

## 10. Conventions

Follows `liquidations` repo patterns: FastAPI + static dir like `src/visualizer/`, reuses existing engine/feed modules unchanged, pytest under `tests/`, files kept focused and well under the 500-line guideline.
