# RSI 4h Trendline-Break Forward Test — Design Spec

**Date:** 2026-06-26
**Status:** Approved (design); pending implementation plan
**Author:** Robert van Rijn (with Claude)

## Purpose

Forward paper-trade the one strategy in the RSI investigation that survived rigorous
validation: the **4h RSI trendline-break on BTC and ETH**. Backtesting can't go further —
the goal now is real-time, out-of-sample confirmation that the edge persists going
forward, plus a record of how the cleanest tradeable version behaves day to day.

**Why this strategy.** On 1h the signal is a coin-flip after costs. On 4h the raw-signal
stop×exit grid is majority-green, and it **survived out-of-sample across 4 independent
BTC windows (2018–26) and ETH, AND beat a random-entry drift control** (same trade
count, same long/short mix, identical exits) in 6/7 cases — strongest on BTC and ETH.
It failed on BNB and was weak on SOL, so this test is **BTC + ETH only**. See memory
`rsi-mean-reversion-findings.md` and `docs/rsi-4h-oos-drift.png`.

**Success criterion.** After a multi-week/month run, we can compare live realized
performance (TP+3% bracket) against the backtest expectation, see whether the edge holds
forward, and observe how often the slower "let it run" (hold-48) variant would have done
better — all without risking capital.

## Locked Strategy Parameters

| Param | Value |
|---|---|
| Assets | BTC/USDT, ETH/USDT (Binance) — **no SOL/BNB** (failed validation) |
| Timeframe | 4h; signals on **closed** bars only |
| Indicator | Wilder RSI(14), strict 3-bar pivots (PIVOT_LEFT=PIVOT_RIGHT=3) |
| Signal | trendline through the **last 2 pivots**; bullish break (RSI crosses above descending resistance) → LONG; bearish (crosses below ascending support) → SHORT |
| **Entry filter** | **none — trade every break.** No EMA200 filter (that was the 1h idea that failed OOS; on 4h the *raw* signal carries the edge) |
| Entry | market / taker, at the break bar's **close** |
| **Live exit (traded)** | bracket: **TP +3%**, **stop −2%**, **8-day (48-bar) time cap** |
| **Shadow exit (logged only)** | **hold-48**: hold to 48 bars (or −2% stop), no TP |
| Intrabar rule | stop checked before TP when both touch in one bar (pessimistic); uses bar high/low |
| Fees | 0.02%/side taker, both legs, applied to live and shadow |
| Sizing | $5,000 notional/trade, independent; cumulative P&L tracked |
| Concurrency | **one open position per asset**; a break firing while that asset is in a position is logged as `SKIP` (not traded) |

Out of scope (YAGNI): EMA200/any entry filter, leverage, SOL/BNB, live order execution
(paper only), websockets, position pyramiding, dynamic sizing.

## Architecture — Approach A (periodic poll)

A single new file `RSI/rsi_4h_forward.py`, reusing `find_pivots`/`detect_break`/
`calculate_rsi` from `RSI/rsi_dashboard.py`, `fetch_ohlcv` from `RSI/rsi_backtest.py`,
and the `Journal` *pattern* from `RSI/rsi_paper.py`. Because 4h entries are deterministic
bar-close fills, there is **no websocket and no resting-order/fill machinery** — the tool
just checks each 4h close. Four focused, independently testable units:

**Reuse caveats (correctness-critical — verified in spec review):**
- `calculate_rsi` MUST be imported from `rsi_dashboard.py`, **not** `rsi_backtest.py` —
  the two implementations differ in seeding/indexing, and `find_pivots`/`detect_break`
  were validated against the dashboard's. Using the wrong one breaks signal fidelity.
- `fetch_ohlcv(symbol, timeframe, since_ms)` is paginated by **`since_ms`**, not a
  `limit`. "Fetch the last ~300 closed 4h candles" means `since_ms = now − ~300×4h`.
- The reused `Journal` is the **pattern** (append-only JSONL + JSON state file + summary
  line), not the class verbatim — its maker-fill counters (`entry_arms`, `exit_fills`,
  `missed_exit_to_stop`) don't apply. Write new counters for SIGNAL/ENTRY/EXIT/SKIP/
  SHADOW/GAP.

| Unit | Responsibility | Depends on |
|---|---|---|
| `Scanner` | given a candle series, return the break (side) on the latest **closed** bar, with RSI / trendline value / `cleared_by` | `rsi_dashboard` funcs |
| `PaperBook` | **the core (TDD'd)**: one position per asset; on each new closed bar apply stop→TP→cap; book closed trades; compute the hold-48 **shadow** for every entry; $5k/trade, 0.02%/side | — |
| `Journal` (reused) | append JSONL events (SIGNAL/ENTRY/EXIT/SKIP/SHADOW/GAP) + state file + summary line | — |
| `run_once(assets)` | one poll: per asset fetch latest 4h candles, advance the book over any new closed bars, detect+enter a new break, log; **idempotent** (tracks last-processed bar ts) | Scanner, PaperBook, Journal |

CLI: `once` (single poll — for cron), `run` (loop, sleep to next 4h close), `replay <days>`
(offline reconcile vs the backtest bracket cell), `status` (print summary from state).

## Data Flow

**Each poll (`run_once`):**
1. For each asset, fetch the last ~300 closed 4h candles (ample for RSI warmup + recent
   pivots). Drop the in-progress final candle; the last **closed** bar is the signal
   candidate.
2. Compute RSI + pivots; **advance any open position** over every closed bar newer than
   `last_processed_ts` (check −2% stop, then +3% TP, then 8-day cap; advance the shadow
   in parallel). Book exits.
3. If the asset is **flat** and the latest closed bar is a break → **enter** at that bar's
   close (record entry, place the conceptual bracket + shadow). If **in a position** and a
   break fires → log `SKIP`.
4. Update `last_processed_ts` per asset, persist state, emit events.

**Idempotency:** re-running `once` on the same bar must not double-book — only bars with
ts > `last_processed_ts[asset]` are acted on. Safe for cron retries and `run`-loop restarts.

## Persistence & Metrics

Two files in `RSI/data/` (gitignored): `rsi_4h_events.jsonl` (every SIGNAL/ENTRY/EXIT/
SKIP/SHADOW/GAP with ts, asset, side, prices, reason) and `rsi_4h_state.json` (open
positions per asset, `last_processed_ts` per asset, cumulative counters).

**Summary line** (on each trade close and on `status`):
```
BTC+ETH 18d  trades 14  win 43%  net $+612  | shadow hold48 net $+880  | open: ETH LONG +1.2%  skipped 3
```
Headline metric = **live TP+3% net + win rate**; the **hold-48 shadow net** sits beside it
so we can see whether "let it run" would have beaten the bracket — without trading it.

## Error Handling

- Fetch failure → retry with backoff; skip the poll cleanly (no state change), try next.
- Malformed/short candle data → log + skip that asset's poll; never crash the loop.
- Process restart → resume from `rsi_4h_state.json`; indicators recomputed from a fresh
  fetch (stateless given the series), only position/counter state restored.
- Missed polls (downtime spanning bars) → on next poll, the book advances over **all**
  closed bars since `last_processed_ts`, so stops/TPs that would have triggered during the
  gap are still detected from the bars' high/low. A `GAP` event notes the downtime.

## Testing

Pure, deterministic core → real tests (TDD `PaperBook`):
- entry books a position at the bar close; one-position-per-asset (second break → SKIP).
- exits: +3% TP, −2% stop, 8-day cap; stop-before-TP when both touch one bar.
- shadow hold-48 computed and logged for every entry, independent of the live exit.
- fees applied both legs, both live and shadow; long and short P&L signs correct.
- `run_once` idempotency: re-processing the same bar books nothing new.
- `Scanner` equivalence: signals match the headless backtest scanner on the same series.
- **Replay mode** (`replay <days>`): pipe historical 4h candles through the same engine
  and reconcile realized TP+3% net against the backtest's BTC/ETH TP+3% grid cell.

## Open Questions / Future Work

- Deployment (cron on VPS vs local `run` loop) is the user's call post-build; the tool
  supports both. Not part of this spec.
- If the forward record confirms the edge, natural extensions: position sizing, a SOL/BNB
  diagnostic to understand why they failed, and graduating to a live (non-paper) executor.
