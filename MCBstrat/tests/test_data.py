import pandas as pd
from mcbstrat.data import resample_to_8h

def test_resample_to_8h_aligns_to_utc_origin_and_aggregates_ohlcv():
    # 16 hourly bars starting 2024-01-01 00:00 UTC -> two full 8H buckets
    idx = pd.date_range("2024-01-01 00:00", periods=16, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open":   range(100, 116),
        "high":   [v + 5 for v in range(100, 116)],
        "low":    [v - 5 for v in range(100, 116)],
        "close":  [v + 1 for v in range(100, 116)],
        "volume": [1.0] * 16,
    }, index=idx)

    out = resample_to_8h(df)

    assert list(out.index) == [
        pd.Timestamp("2024-01-01 00:00", tz="UTC"),
        pd.Timestamp("2024-01-01 08:00", tz="UTC"),
    ]
    assert out.loc["2024-01-01 00:00", "open"] == 100
    assert out.loc["2024-01-01 00:00", "close"] == 108
    assert out.loc["2024-01-01 00:00", "high"] == 112
    assert out.loc["2024-01-01 00:00", "low"] == 95
    assert out.loc["2024-01-01 00:00", "volume"] == 8.0
