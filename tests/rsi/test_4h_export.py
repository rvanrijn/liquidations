"""Tests for the per-trade CSV export row-builder (_trade_rows)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../RSI"))

from rsi_4h_forward import _trade_rows, ClosedTrade, EXPORT_COLS, CAPITAL

DAY = 86_400_000
H4 = 4 * 3600 * 1000


def _t(asset, side, ep, xp, e_ts, x_ts, reason, ret, pnl, kind="live"):
    return ClosedTrade(asset, side, ep, xp, e_ts, x_ts, reason, ret, pnl, kind)


def test_rows_have_all_columns_and_running_equity():
    trades = [
        _t("BTC/USDT", "LONG", 100.0, 103.0, 0, 5 * H4, "TP", 0.0296, 148.0),
        _t("ETH/USDT", "LONG", 200.0, 196.0, 10 * H4, 12 * H4, "STOP", -0.0204, -102.0),
    ]
    rows = _trade_rows(trades)
    assert [c for c in rows[0]] == EXPORT_COLS              # every column present, in order
    assert rows[0]["n"] == 1 and rows[1]["n"] == 2
    # running equity compounds chronologically off CAPITAL
    assert abs(rows[0]["cum_equity_usd"] - (CAPITAL + 148.0)) < 1e-6
    assert abs(rows[1]["cum_equity_usd"] - (CAPITAL + 148.0 - 102.0)) < 1e-6
    assert rows[0]["win"] == 1 and rows[1]["win"] == 0


def test_long_bracket_and_hold_time():
    r = _trade_rows([_t("BTC/USDT", "LONG", 100.0, 103.0, 0, 5 * H4, "TP", 0.0296, 148.0)])[0]
    assert abs(r["tp_price"] - 103.0) < 1e-6 and abs(r["sl_price"] - 98.0) < 1e-6   # +3% / −2%
    assert r["bars_held"] == 5 and r["hold_hours"] == 20
    assert abs(r["gross_move_pct"] - 3.0) < 1e-3                                      # (103/100 - 1)


def test_short_bracket_and_directional_gross():
    # SHORT: TP below entry, SL above; profit when price falls
    r = _trade_rows([_t("ETH/USDT", "SHORT", 200.0, 194.0, 0, 3 * H4, "TP", 0.0296, 148.0)])[0]
    assert abs(r["tp_price"] - 194.0) < 1e-6 and abs(r["sl_price"] - 204.0) < 1e-6   # −3% / +2%
    assert abs(r["gross_move_pct"] - (200 / 194 - 1) * 100) < 1e-3   # directional: profit as price falls
    assert r["gross_move_pct"] > 0 and r["win"] == 1                  # short gain is positive


def test_empty_input_yields_no_rows():
    assert _trade_rows([]) == []


def test_rows_sorted_by_entry_time():
    trades = [
        _t("BTC/USDT", "LONG", 100.0, 103.0, 20 * H4, 25 * H4, "TP", 0.03, 150.0),
        _t("ETH/USDT", "LONG", 200.0, 206.0, 1 * H4, 4 * H4, "TP", 0.03, 150.0),
    ]
    rows = _trade_rows(trades)
    assert rows[0]["asset"] == "ETH/USDT" and rows[1]["asset"] == "BTC/USDT"   # earlier entry first


def test_kind_column_default_live():
    r = _trade_rows([_t("BTC/USDT", "LONG", 100.0, 103.0, 0, 5 * H4, "TP", 0.03, 150.0)])[0]
    assert r["kind"] == "live"


def test_each_kind_has_its_own_equity_track():
    # live + hold-48 + ladder on the same signal → three independent equity curves
    trades = [
        _t("BTC/USDT", "LONG", 100.0, 103.0, 0, 5 * H4, "TP", 0.03, 150.0, kind="live"),
        _t("BTC/USDT", "LONG", 100.0, 110.0, 0, 48 * H4, "HOLD", 0.10, 500.0, kind="shadow"),
        _t("BTC/USDT", "LONG", 100.0, 100.0, 0, 30 * H4, "CAP", 0.055, 275.0, kind="ladder"),
    ]
    eq = {r["kind"]: r["cum_equity_usd"] for r in _trade_rows(trades)}
    assert eq["live"] == CAPITAL + 150.0       # each starts from CAPITAL, not additive
    assert eq["shadow"] == CAPITAL + 500.0
    assert eq["ladder"] == CAPITAL + 275.0


def test_ladder_blanks_exit_price_and_gross():
    # ladder books exit_price == entry_price (two-leg) → those cells are blank, net carries P&L
    r = _trade_rows([_t("BTC/USDT", "LONG", 100.0, 100.0, 0, 30 * H4, "CAP", 0.055, 275.0, kind="ladder")])[0]
    assert r["exit_price"] == "" and r["gross_move_pct"] == ""
    assert abs(r["net_return_pct"] - 5.5) < 1e-6 and r["pnl_usd"] == 275.0
