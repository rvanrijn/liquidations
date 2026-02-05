# Automated BTC Scalping System (1–5 Minute, Liquidity + Order Flow)

This document defines a **fully rule-based automated scalping system** for BTC using:
- Order flow
- Liquidation hotzones
- Short-term delta
- Strict risk control

The system is designed for **1–5 minute scalps**, not swing trading.

---

## 1. Objective

- Trade BTC on 1–5 minute timeframe
- Capture short-term moves toward liquidation liquidity
- Use leverage safely (10–20×)
- Preserve capital through strict risk rules
- Eliminate emotional decision-making

---

## 2. Core Inputs (No Indicators)

The strategy uses **only objective market data**:

1. Net Delta (5m, 15m)
2. Buy vs Sell Pressure (%)
3. Long Liquidation Zones (below price)
4. Short Liquidation Zones (above price)
5. Price location (VWAP, range high/low)
6. Large trade tape (optional confirmation)

No RSI, MACD, EMA, etc.

---

## 3. Liquidation Hotzones (Key Concept)

- **Long liquidation zones (below price)** = downside fuel
- **Short liquidation zones (above price)** = upside fuel

Price is statistically attracted to the side with:
- More liquidation value
- Closer distance
- Supporting order flow

Liquidation zones are **targets**, not entry signals.

---

## 4. Directional Bias Engine

Bias is recalculated every **30–60 seconds**.

### SHORT Bias Conditions

```text
IF
    Long_Liq_Value ≥ 2 × Short_Liq_Value
AND 15m_Delta < 0
AND Price ≤ VWAP
THEN
    Bias = SHORT
```

### LONG Bias Conditions

```text
IF
    Short_Liq_Value ≥ 2 × Long_Liq_Value
AND 15m_Delta > 0
AND Price ≥ VWAP
THEN
    Bias = LONG
```

If conditions are not met → **NO TRADE**

---

## 5. Entry Rules

### SHORT Entry (Primary Setup)

```text
Bias == SHORT
AND Price within 0.1–0.3% of local high
AND 5m Delta flips negative
AND Buy Pressure < 50%
AND No large aggressive buys in last 30 seconds
→ Enter SHORT
```

### LONG Entry (Countertrend / Liquidity Sweep)

```text
Bias == LONG
AND Price within 0.3–0.5% of short liquidation zone
AND Delta flips strongly positive
AND Price stops making lower lows
AND Aggressive buying appears
→ Enter LONG (scalp only)
```

---

## 6. Risk Management (Non-Negotiable)

### Per Trade

| Parameter | Value |
|-----------|-------|
| Risk per trade | 0.5–1% of account |
| Leverage | 10–20× |
| Max stop distance | 0.25% |
| Stop Loss | Entry ± 0.25% |

### Daily Limits

| Parameter | Value |
|-----------|-------|
| Max trades per day | 6 |
| Max daily loss | –2R |

If daily loss cap is hit → **BOT SHUTS DOWN**

---

## 7. Take Profit Logic

### Standard Targets

- **TP1** = +0.3% (partial exit, 30–50%)
- **TP2** = Nearest liquidation cluster

### Early Exit (Capital Protection)

IF Delta flips against position before TP1 → **Exit immediately**

No holding and hoping.

---

## 8. Trade Management Rules

- Never widen stop
- Never add to losing trade
- Never trade without bias
- Never trade during major news events
- Never trade low-liquidity hours

---

## 9. Execution Architecture (Python)

### Data Layer

- WebSocket: trades, liquidations, order flow
- Rolling aggregation: 1m / 5m / 15m

### Strategy Engine

- Bias calculator
- Entry validator
- Exit monitor

### Execution Layer

- Limit orders for entries (maker when possible)
- Market orders for exits / stops

### Safety Layer

- Kill switch
- Slippage guard
- Max leverage guard
- Exchange disconnect protection

---

## 10. Example Strategy Pseudocode

```python
if bias == "SHORT":
    if near_local_high() \
       and delta_5m < 0 \
       and buy_pressure < 50 \
       and liq_below > 2 * liq_above:

        enter_short(
            size=calc_position_size(account, risk=0.01),
            stop=entry_price * 1.0025,
            tp1=entry_price * 0.997,
            tp2=nearest_long_liq
        )
```

---

## 11. Expected Performance (Realistic)

| Metric | Value |
|--------|-------|
| Trades/day | 3–6 |
| Win rate | ~50–55% |
| RR | 2–3R |
| Daily expectancy | +1–2% |
| Drawdowns | controlled and survivable |

Automation does not increase edge — it preserves it.

---

## 12. When NOT to Trade

- Major macro news (CPI, FOMC)
- Extremely low volatility
- Extreme volatility spikes
- Exchange instability
- Data feed degradation

---

## 13. Final Principles

- **Liquidation zones** = targets
- **Order flow** = confirmation
- **Risk control** = survival
- **Discipline > intelligence**
- **Consistency beats speed**

> This system is designed to stay alive first, grow second.