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

def money_flow(df: pd.DataFrame, period: int = MFI_PERIOD, mult: float = MFI_MULTIPLIER) -> pd.Series:
    """VuManChu MFI+RSI area on HA candles. >0 green, <0 red.

    sma(((ha_close - ha_open) / (ha_high - ha_low)) * mult, period).
    The RSI/Y-offset constants in VuManChu are display-only and do not change the sign.
    """
    rng = (df["ha_high"] - df["ha_low"]).replace(0, np.nan)
    raw = ((df["ha_close"] - df["ha_open"]) / rng) * mult
    return raw.rolling(period).mean()

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
