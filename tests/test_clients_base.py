# tests/test_clients_base.py
import pytest
from src.clients.base import BaseExchangeClient
from src.models import LiquidationEvent


class MockClient(BaseExchangeClient):
    exchange_name = "mock"
    ws_url = "wss://mock.example.com/ws"

    def parse_message(self, data: dict) -> LiquidationEvent | None:
        if "liquidation" in data:
            return LiquidationEvent(
                exchange=self.exchange_name,
                coin=data["liquidation"]["coin"],
                side=data["liquidation"]["side"],
                size=1.0,
                price=100.0,
                value_usd=100.0,
                timestamp=1700000000000,
                wallet=None,
            )
        return None

    def get_subscribe_message(self) -> dict:
        return {"action": "subscribe"}


def test_client_has_required_attributes():
    client = MockClient(coins=["BTC", "ETH"], on_event=lambda e: None)
    assert client.exchange_name == "mock"
    assert client.ws_url == "wss://mock.example.com/ws"
    assert client.coins == ["BTC", "ETH"]


def test_parse_message_returns_event():
    client = MockClient(coins=["BTC"], on_event=lambda e: None)
    event = client.parse_message({"liquidation": {"coin": "BTC", "side": "long"}})
    assert event is not None
    assert event.coin == "BTC"


def test_parse_message_returns_none_for_invalid():
    client = MockClient(coins=["BTC"], on_event=lambda e: None)
    event = client.parse_message({"other": "data"})
    assert event is None
