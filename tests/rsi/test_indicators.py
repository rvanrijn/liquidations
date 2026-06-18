import numpy as np
from rsi_backtest import calculate_rsi, ema
from rsi_paper import Indicators


def _ramp(n):
    # deterministic non-monotonic series so RSI is well-defined
    return [100 + 10 * np.sin(i / 5.0) for i in range(n)]


def test_not_ready_until_ema_period():
    ind = Indicators(rsi_period=14, ema_period=200)
    ind.seed(_ramp(150))
    assert ind.ready is False
    ind.seed(_ramp(1000))
    assert ind.ready is True


def test_rsi_ema_match_backtest_functions():
    closes = _ramp(1000)
    ind = Indicators(rsi_period=14, ema_period=200)
    ind.seed(closes)
    arr = np.array(closes, dtype=float)
    assert ind.rsi == calculate_rsi(arr, 14)[-1]
    assert ind.ema == ema(arr, 200)[-1]


def test_update_appends_and_recomputes():
    closes = _ramp(1000)
    ind = Indicators(rsi_period=14, ema_period=200)
    ind.seed(closes)
    ind.update(123.0)
    arr = np.array(closes + [123.0], dtype=float)
    assert ind.rsi == calculate_rsi(arr, 14)[-1]
    assert ind.ema == ema(arr, 200)[-1]


def test_buffer_is_bounded():
    ind = Indicators(rsi_period=14, ema_period=200, max_buffer=1200)
    ind.seed(_ramp(1200))
    for i in range(500):
        ind.update(100.0 + i)
    assert len(ind._closes) <= 1200
