"""Flat-between position state machine over green/red dots and money-flow sign."""
import pandas as pd

def build_signals(df: pd.DataFrame, arm_window: int = 0) -> pd.DataFrame:
    """df needs columns: green (bool), red (bool), mf (float).

    A dot ARMS a position; entry fires on the first bar within the arming window where
    money-flow confirms (long: mf>0, short: mf<0). The window spans the dot bar plus
    ``arm_window`` following bars, so:
      - arm_window=0  -> dot and MF must agree on the SAME bar (original strict rule).
      - arm_window=N  -> MF may confirm up to N bars after the dot.
    A fresh dot re-arms (resets the countdown). Exits are unchanged: long exits on mf<0,
    short exits on mf>0. Flat-between, mutually exclusive — a long exit never auto-opens a
    short; the opposite side needs its own armed dot.

    Returns df + 'position' (int in {-1,0,1}, the state AS OF this closed bar) and
    'event' (str: ENTER_LONG/EXIT_LONG/ENTER_SHORT/EXIT_SHORT/'').
    """
    positions, events = [], []
    pos = 0
    long_arm = -1   # bars of life remaining for a long arm; >=0 means armed this bar
    short_arm = -1
    for green, red, mf in zip(df["green"], df["red"], df["mf"]):
        if green:
            long_arm = arm_window
        if red:
            short_arm = arm_window
        event = ""
        if pos == 0:
            if long_arm >= 0 and mf > 0:
                pos, event = 1, "ENTER_LONG"
                long_arm = short_arm = -1
            elif short_arm >= 0 and mf < 0:
                pos, event = -1, "ENTER_SHORT"
                long_arm = short_arm = -1
        elif pos == 1:
            if mf < 0:
                pos, event = 0, "EXIT_LONG"
        elif pos == -1:
            if mf > 0:
                pos, event = 0, "EXIT_SHORT"
        if long_arm >= 0:
            long_arm -= 1
        if short_arm >= 0:
            short_arm -= 1
        positions.append(pos)
        events.append(event)
    out = df.copy()
    out["position"] = positions
    out["event"] = events
    return out
