import pandas as pd
from mcbstrat.heikin_ashi import add_heikin_ashi

def test_heikin_ashi_first_two_bars_hand_computed():
    idx = pd.date_range("2024-01-01", periods=2, freq="8h", tz="UTC")
    df = pd.DataFrame({
        "open":  [100.0, 110.0],
        "high":  [120.0, 130.0],
        "low":   [ 90.0, 100.0],
        "close": [110.0, 120.0],
        "volume":[1.0, 1.0],
    }, index=idx)

    out = add_heikin_ashi(df)

    # Bar 0: ha_close=(100+120+90+110)/4=105 ; ha_open seed=(open+close)/2=(100+110)/2=105
    assert out.iloc[0]["ha_close"] == 105.0
    assert out.iloc[0]["ha_open"] == 105.0
    # Bar 1: ha_close=(110+130+100+120)/4=115 ; ha_open=(prev ha_open+prev ha_close)/2=(105+105)/2=105
    assert out.iloc[1]["ha_close"] == 115.0
    assert out.iloc[1]["ha_open"] == 105.0
    assert out.iloc[1]["ha_high"] == max(130.0, 105.0, 115.0)
    assert out.iloc[1]["ha_low"] == min(100.0, 105.0, 115.0)
    # raw columns preserved untouched
    assert out.iloc[1]["open"] == 110.0
    assert out.iloc[1]["close"] == 120.0
