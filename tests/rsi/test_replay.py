from rsi_paper import run_replay


def _down_then_up(n=1000):
    closes = [100.0] * (n - 60)
    closes += [100.0 - i * 0.3 for i in range(30)]   # drop -> RSI low
    closes += [closes[-1] + i * 0.5 for i in range(30)]  # rally -> RSI high
    return closes


def test_replay_runs_and_reports():
    closes = _down_then_up()
    # candles: [ts, open, high, low, close, vol]; proxy fills use low/high
    candles = []
    for i, c in enumerate(closes):
        candles.append([i * 60_000, c, c + 0.5, c - 0.5, c, 1.0])
    result = run_replay(candles)
    assert "balance" in result
    assert result["entry_arms"] >= 0
