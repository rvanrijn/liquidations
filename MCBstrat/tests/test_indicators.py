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
    ha_hlc3 = pd.Series(30000 + np.cumsum(rng.normal(0, 50, n)), index=idx)

    wt1, wt2 = wavetrend(ha_hlc3)
    ref1, ref2 = _reference_wavetrend(ha_hlc3)

    pd.testing.assert_series_equal(wt1.dropna(), ref1.dropna(), check_names=False)
    pd.testing.assert_series_equal(wt2.dropna(), ref2.dropna(), check_names=False)

from mcbstrat.indicators import money_flow

def test_money_flow_sign_follows_ha_body_direction():
    n = 80
    idx = pd.date_range("2024-01-01", periods=n, freq="8h", tz="UTC")
    df = pd.DataFrame({
        "ha_open":  [100.0] * n,
        "ha_high":  [110.0] * n,
        "ha_low":   [ 99.0] * n,
        "ha_close": [109.0] * n,
    }, index=idx)
    mf = money_flow(df)
    assert mf.iloc[-1] > 0
    df2 = df.copy()
    df2["ha_open"], df2["ha_close"] = 109.0, 100.0
    mf2 = money_flow(df2)
    assert mf2.iloc[-1] < 0
