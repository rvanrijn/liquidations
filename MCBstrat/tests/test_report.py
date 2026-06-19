from mcbstrat.report import render_markdown

def test_report_contains_required_sections():
    payload = {
        "symbol": "BTCUSDT", "bars": 3200, "span": "2023-01-01 .. 2026-06-19",
        "cost": {"all": {"n": 40, "win_rate": 0.5, "profit_factor": 1.2, "expectancy": 0.01,
                         "total_return": 0.4, "max_dd": -0.2, "avg_bars": 6},
                 "long": {"n": 8}, "short": {"n": 32}},
        "zero_cost": {"all": {"total_return": 0.6}},
        "verdict_long": "inconclusive / insufficient power",
        "verdict_short": "negative edge",
        "oos": {"in": {"expectancy": 0.012}, "out": {"expectancy": 0.004}},
        "regimes": {"BULL": {"n": 20, "expectancy": 0.02}, "BEAR": {"n": 20, "expectancy": -0.01}},
        "tv_crosscheck": "not run",
    }
    md = render_markdown(payload)
    for needle in ["Trade count", "Long", "Short", "Out-of-sample", "Regime",
                   "Zero-cost", "Verdict", "insufficient power"]:
        assert needle in md
