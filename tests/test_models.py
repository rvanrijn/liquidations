from src.models import LiquidationEvent


def test_liquidation_event_creation():
    event = LiquidationEvent(
        exchange="bybit",
        coin="BTC",
        side="long",
        size=1.5,
        price=90000.0,
        value_usd=135000.0,
        timestamp=1700000000000,
        wallet="0xabc123",
    )
    assert event.exchange == "bybit"
    assert event.coin == "BTC"
    assert event.side == "long"
    assert event.value_usd == 135000.0


def test_liquidation_event_wallet_optional():
    event = LiquidationEvent(
        exchange="binance",
        coin="ETH",
        side="short",
        size=10.0,
        price=3400.0,
        value_usd=34000.0,
        timestamp=1700000000000,
        wallet=None,
    )
    assert event.wallet is None
