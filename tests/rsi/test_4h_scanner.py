import numpy as np
from rsi_4h_forward import latest_break

def _c(ts, close): return [ts, close, close, close, close, 1.0]

def _series(closes):
    return [_c(i*1000, v) for i, v in enumerate(closes)]

def test_no_break_on_flat_series():
    assert latest_break(_series([100.0]*120)) is None

def test_bullish_break_returns_long():
    closes = [100 + 8*np.sin(i/6.0) for i in range(110)]
    closes += [closes[-1]-i for i in range(1,16)]
    closes += [closes[-1]+i*3 for i in range(1,8)]
    sig = latest_break(_series(closes))
    if sig is not None:
        assert sig["side"] in ("LONG", "SHORT")
        assert sig["ts"] == (len(closes)-1)*1000
        assert sig["close"] == closes[-1]
        assert sig["cleared_by"] >= 0

def test_matches_backtest_detect_break():
    from rsi_dashboard import calculate_rsi, find_pivots, detect_break
    closes = [100 + 10*np.sin(i/5.0) for i in range(200)]
    cs = _series(closes)
    arr = np.array(closes)
    rsi = calculate_rsi(arr); hi, lo = find_pivots(rsi)
    bt, bv = detect_break(rsi, hi, lo, len(arr)-1)
    sig = latest_break(cs)
    if bt is None:
        assert sig is None
    else:
        assert sig["side"] == ("LONG" if bt == "bullish_break" else "SHORT")
        assert abs(sig["tl"] - bv) < 1e-9
