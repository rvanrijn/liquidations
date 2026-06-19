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
