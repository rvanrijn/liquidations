# tests/orderflow/test_models.py
from src.orderflow.models import TradeEvent


def test_trade_event_creation_with_all_fields():
    """Test TradeEvent creation with all required fields."""
    event = TradeEvent(
        exchange="bybit",
        coin="BTC",
        side="buy",
        size=0.5,
        price=90000.0,
        value_usd=45000.0,
        timestamp=1700000000000,
        size_category="large",
    )
    assert event.exchange == "bybit"
    assert event.coin == "BTC"
    assert event.side == "buy"
    assert event.size == 0.5
    assert event.price == 90000.0
    assert event.value_usd == 45000.0
    assert event.timestamp == 1700000000000
    assert event.size_category == "large"


def test_trade_event_sell_side():
    """Test TradeEvent with sell side."""
    event = TradeEvent(
        exchange="binance",
        coin="ETH",
        side="sell",
        size=10.0,
        price=3400.0,
        value_usd=34000.0,
        timestamp=1700000000000,
        size_category="medium",
    )
    assert event.side == "sell"
    assert event.size_category == "medium"


def test_trade_event_whale_category():
    """Test TradeEvent with whale size category."""
    event = TradeEvent(
        exchange="bybit",
        coin="BTC",
        side="buy",
        size=20.0,
        price=90000.0,
        value_usd=1800000.0,
        timestamp=1700000000000,
        size_category="whale",
    )
    assert event.size_category == "whale"


def test_trade_event_small_category():
    """Test TradeEvent with small size category."""
    event = TradeEvent(
        exchange="binance",
        coin="SOL",
        side="sell",
        size=50.0,
        price=100.0,
        value_usd=5000.0,
        timestamp=1700000000000,
        size_category="small",
    )
    assert event.size_category == "small"
