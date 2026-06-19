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
