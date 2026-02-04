# tests/test_clients_bybit.py
from src.clients.bybit import BybitClient


def test_bybit_client_attributes():
    client = BybitClient(coins=["BTC", "ETH"], on_event=lambda e: None)
    assert client.exchange_name == "bybit"
    assert "bybit" in client.ws_url


def test_bybit_subscribe_message():
    client = BybitClient(coins=["BTC", "ETH"], on_event=lambda e: None)
    msg = client.get_subscribe_message()
    assert msg["op"] == "subscribe"
    assert "allLiquidation.BTCUSDT" in msg["args"]
    assert "allLiquidation.ETHUSDT" in msg["args"]


def test_bybit_parse_liquidation():
    client = BybitClient(coins=["BTC"], on_event=lambda e: None)

    # Bybit allLiquidation message format
    data = {
        "topic": "allLiquidation.BTCUSDT",
        "type": "snapshot",
        "ts": 1700000000000,
        "data": {
            "T": 1700000000000,
            "s": "BTCUSDT",
            "S": "Buy",  # Buy = liquidated long
            "v": "0.5",
            "p": "90000.00",
        },
    }

    event = client.parse_message(data)
    assert event is not None
    assert event.exchange == "bybit"
    assert event.coin == "BTC"
    assert event.side == "long"
    assert event.price == 90000.0
    assert event.size == 0.5
    assert event.value_usd == 45000.0


def test_bybit_parse_short_liquidation():
    client = BybitClient(coins=["ETH"], on_event=lambda e: None)

    data = {
        "topic": "allLiquidation.ETHUSDT",
        "type": "snapshot",
        "data": {
            "T": 1700000000000,
            "s": "ETHUSDT",
            "S": "Sell",  # Sell = liquidated short
            "v": "10.0",
            "p": "3400.00",
        },
    }

    event = client.parse_message(data)
    assert event is not None
    assert event.side == "short"


def test_bybit_parse_irrelevant_message():
    client = BybitClient(coins=["BTC"], on_event=lambda e: None)
    event = client.parse_message({"op": "subscribe", "success": True})
    assert event is None
