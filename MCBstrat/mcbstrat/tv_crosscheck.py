"""MANUAL: compare Python WaveTrend/MoneyFlow sign to TradingView's VuManChu plot.

Run interactively (not under pytest). Procedure the operator/agent follows with the
TradingView MCP tools:

  1. chart_set_symbol BTCUSDT (Bybit) ; chart_set_timeframe 8H ; chart_set_type Heikin Ashi
  2. Add the 'VMC Cipher_B_Divergences' (VuManChu) indicator to the chart.
  3. For ~10 recent CLOSED bars (optionally via replay_step), read:
       - data_get_study_values -> VuManChu wt1, wt2, MFI-area sign
  4. Compute the same bars in Python from load_btc_8h() + indicators, and compare:
       - sign(wt1-wt2) match, green/red dot match, MF sign match.
  5. Record the match rate; paste it into the report's 'TV cross-check' line.

This file intentionally has no automated test: it depends on a live MCP session and is a
validation gate, per the spec (Section 5 note + Section 10 DoD).
"""

def compare(py_rows: list[dict], tv_rows: list[dict]) -> dict:
    """Pure helper: each row dict has keys wt_sign, dot, mf_sign. Returns match rates."""
    assert len(py_rows) == len(tv_rows)
    n = len(py_rows) or 1
    wt = sum(p["wt_sign"] == t["wt_sign"] for p, t in zip(py_rows, tv_rows)) / n
    dot = sum(p["dot"] == t["dot"] for p, t in zip(py_rows, tv_rows)) / n
    mf = sum(p["mf_sign"] == t["mf_sign"] for p, t in zip(py_rows, tv_rows)) / n
    return {"wt_match": wt, "dot_match": dot, "mf_match": mf}
