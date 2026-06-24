# Battle Strategy Backtester Design

## Problem

The magnet hypothesis has a 77-98% battle win rate, but live trades lose money. The disconnect:
1. **Late entry** — OI gate fires after price already moved 0.1-0.25% from snapshot
2. **Exit mismatch** — battle resolves ±0.5% from snapshot, not from entry price
3. **No systematic testing** — only 14 live trades, no backtester to sweep parameters

## Solution: Two-Phase Backtester

### Phase 1: DB Battle Sweep

Quick parameter sweep on 698 existing battles from `data/magnet_battles.db` (Feb 10 - Mar 6).

**Parameters swept:**

| Parameter | Values |
|-----------|--------|
| Resolve % | 0.3, 0.4, 0.5, 0.6, 0.75, 1.0 |
| OI gate | none, -0.1%, -0.2%, -0.3%, -0.5% |
| Min imbalance | 1.1, 1.25, 1.5, 1.75, 2.0 |
| Entry slippage | 0.0%, 0.05%, 0.10%, 0.15%, 0.20%, 0.25% |

900 combinations, each against 698 battles, in-memory.

**Per-combination metrics:**
- Trades, wins, losses, win rate
- Total P&L % and USD ($2,300 notional)
- Profit factor (gross wins / gross losses)
- Max drawdown
- Average trade P&L

**Entry simulation:** For battles matching OI gate + imbalance filter, entry at snapshot + slippage, exit at resolved_price. Fees: 0.05% round-trip (market) or 0.00% (limit maker).

**Output:** Rich table top 20 configs sorted by net P&L. Filter impact summary showing how much each parameter matters.

### Phase 2: Full Replay Backtester

Replay 1m Binance candles + 5m OI through the monitor logic.

**Data:** 1m BTCUSDT candles, 5m OI history from Binance API. Liq levels from battle DB snapshots.

**What Phase 2 adds over Phase 1:**
- Tick-by-tick battle resolution (catches wicks Phase 1 misses)
- Limit order fill rate testing (does price retrace to snapshot?)
- Time-based limit cancellation
- Accurate MFE/MAE tracking

**Output:** Same metrics as Phase 1, plus equity curve and per-trade log.

## File Structure

```
src/krakenbot/backtest.py     — backtester (both phases)
```

CLI: `uv run krakenbot-backtest --phase 1` or `--phase 2 --days 25`

## Implementation Order

1. Phase 1 sweep (quick wins, answers most questions)
2. Phase 2 replay (validates Phase 1 findings with tick data)
