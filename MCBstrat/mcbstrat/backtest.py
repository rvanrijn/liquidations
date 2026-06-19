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
    """df needs raw open/high/low/close and an 'event' column. Returns (trades, equity_series).

    Fill rule: an ENTER/EXIT event decided on closed bar i fills at bar i+1's raw open.
    If an EXIT event lands on the last bar (no i+1), the position is force-closed at the
    final bar's close after the loop -- never at the signal bar's own open (no lookahead).
    """
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

    eq = [1.0]
    for t in trades:
        eq.append(eq[-1] * (1 + t.return_pct))
    equity = pd.Series(eq[1:], index=[t.exit_time for t in trades]) if trades else pd.Series(dtype=float)
    return trades, equity
