# tests/test_clients_binance.py
from src.clients.binance import BinanceClient


def test_binance_client_attributes():
    client = BinanceClient(coins=["BTC", "ETH"], on_event=lambda e: None)
    assert client.exchange_name == "binance"
    assert "binance" in client.ws_url


def test_binance_ws_url_includes_streams():
    client = BinanceClient(coins=["BTC", "ETH", "SOL"], on_event=lambda e: None)
    assert "btcusdt@forceOrder" in client.ws_url
    assert "ethusdt@forceOrder" in client.ws_url
    assert "solusdt@forceOrder" in client.ws_url


def test_binance_parse_liquidation():
    client = BinanceClient(coins=["BTC"], on_event=lambda e: None)

    # Binance forceOrder message format
    data = {
        "stream": "btcusdt@forceOrder",
        "data": {
            "e": "forceOrder",
            "E": 1700000000000,
            "o": {
                "s": "BTCUSDT",
                "S": "SELL",  # SELL = liquidated long
                "o": "LIMIT",
                "f": "IOC",
                "q": "0.5",
                "p": "90000.00",
                "ap": "89950.00",
                "X": "FILLED",
                "l": "0.5",
                "z": "0.5",
                "T": 1700000000000,
            },
        },
    }

    event = client.parse_message(data)
    assert event is not None
    assert event.exchange == "binance"
    assert event.coin == "BTC"
    assert event.side == "long"
    assert event.price == 89950.0  # Uses average price
    assert event.size == 0.5


def test_binance_parse_short_liquidation():
    client = BinanceClient(coins=["ETH"], on_event=lambda e: None)

    data = {
        "stream": "ethusdt@forceOrder",
        "data": {
            "e": "forceOrder",
            "o": {
                "s": "ETHUSDT",
                "S": "BUY",  # BUY = liquidated short
                "q": "10.0",
                "ap": "3400.00",
                "z": "10.0",
                "T": 1700000000000,
            },
        },
    }

    event = client.parse_message(data)
    assert event is not None
    assert event.side == "short"


def test_binance_parse_irrelevant_message():
    client = BinanceClient(coins=["BTC"], on_event=lambda e: None)
    event = client.parse_message({"result": None, "id": 1})
    assert event is None
