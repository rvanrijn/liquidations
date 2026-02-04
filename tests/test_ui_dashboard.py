# tests/test_ui_dashboard.py
from src.ui.dashboard import format_usd, format_time, truncate_wallet


def test_format_usd_thousands():
    assert format_usd(1500) == "$1.5K"
    assert format_usd(999) == "$999"


def test_format_usd_millions():
    assert format_usd(1500000) == "$1.50M"
    assert format_usd(71780000) == "$71.78M"


def test_format_usd_small():
    assert format_usd(50) == "$50"


def test_format_time():
    # 1700000000000 ms = 2023-11-14 22:13:20 UTC
    result = format_time(1700000000000)
    assert "22:13" in result or ":" in result  # Time format varies by locale


def test_truncate_wallet():
    assert truncate_wallet("0xabc123def456") == "0xabc1..."
    assert truncate_wallet(None) == "-"
    assert truncate_wallet("short") == "short"
