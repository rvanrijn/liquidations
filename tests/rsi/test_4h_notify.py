"""Tests for the Telegram entry-alert path (notifier is best-effort, env-gated)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../RSI"))

import rsi_4h_forward as F
from rsi_4h_forward import Journal, _format_entry_msg, telegram_notify


def test_format_entry_msg_long_directional():
    s = _format_entry_msg({"asset": "BTC/USDT", "side": "LONG", "price": 62400, "tp": 64272, "sl": 61152})
    assert "🟢" in s and "BTC LONG" in s
    assert "62,400" in s and "64,272" in s and "61,152" in s
    # LONG: TP above (price up), SL below (price down)
    assert "▲ +3%" in s and "▼ −2%" in s


def test_format_entry_msg_short_directional():
    s = _format_entry_msg({"asset": "ETH/USDT", "side": "SHORT", "price": 3000, "tp": 2910, "sl": 3060})
    assert "🔴" in s and "ETH SHORT" in s
    # SHORT: TP below entry (price down = profit), SL above (price up)
    assert "▼ −3%" in s and "▲ +2%" in s


def test_notify_is_noop_without_env(monkeypatch):
    monkeypatch.delenv("RSI_TG_TOKEN", raising=False)
    monkeypatch.delenv("RSI_TG_CHAT", raising=False)
    assert telegram_notify("hi") is False     # no token/chat → silent no-op, never raises


def test_record_entry_pushes_only_when_enabled(monkeypatch):
    sent = []
    monkeypatch.setattr(F, "telegram_notify", lambda text: sent.append(text) or True)

    j = Journal()                              # notify_entries defaults False
    j.record("ENTRY", {"asset": "BTC/USDT", "side": "LONG", "price": 100, "tp": 103, "sl": 98})
    assert sent == []                          # silent by default (replay/status/dash)

    j.notify_entries = True
    j.record("ENTRY", {"asset": "BTC/USDT", "side": "LONG", "price": 100, "tp": 103, "sl": 98})
    assert len(sent) == 1 and "BTC LONG" in sent[0]


def test_exit_pushes_only_live_and_only_when_enabled(monkeypatch):
    sent = []
    monkeypatch.setattr(F, "telegram_notify", lambda text: sent.append(text) or True)

    j = Journal(); j.notify_entries = True              # exits NOT enabled yet
    j.record("EXIT", {"asset": "BTC/USDT", "kind": "live", "reason": "TP", "pnl": 50.0, "ret": 0.01})
    assert sent == []                                   # entry-only flag → no exit push

    j.notify_exits = True
    j.record("EXIT", {"asset": "ETH/USDT", "kind": "shadow", "reason": "HOLD", "pnl": 9.0, "ret": 0.1})
    j.record("EXIT", {"asset": "BTC/USDT", "kind": "ladder", "reason": "CAP", "pnl": 9.0, "ret": 0.1})
    assert sent == []                                   # shadow/ladder exits never push
    j.record("EXIT", {"asset": "BTC/USDT", "kind": "live", "reason": "TP", "pnl": 120.0, "ret": 0.024})
    assert len(sent) == 1 and "BTC" in sent[0] and "closed" in sent[0]


def test_format_exit_msg_win_and_loss():
    from rsi_4h_forward import _format_exit_msg
    w = _format_exit_msg({"asset": "BTC/USDT", "side": "SHORT", "reason": "TP", "exit": 58370.0, "pnl": 148.0, "ret": 0.0296})
    assert "✅" in w and "BTC SHORT closed · TP" in w and "$+148" in w
    l = _format_exit_msg({"asset": "ETH/USDT", "side": "SHORT", "reason": "STOP", "exit": 1610.0, "pnl": -100.0, "ret": -0.02})
    assert "❌" in l and "$-100" in l
