"""Integration tests for the krakenbot webhook relay."""

import os

# Set env before importing app
os.environ["BOT_DRY_RUN"] = "true"
os.environ["BOT_DB_PATH"] = "/tmp/test_webhook.db"
os.environ["WEBHOOK_SECRET"] = "test-secret"
os.environ["BOT_LEVERAGE"] = "3"

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.krakenbot.main import app

# Always pretend it's a Wednesday (weekday=2) so Saturday filter doesn't block tests
_FAKE_NOW = datetime(2026, 3, 25, 12, 0, 0, tzinfo=timezone.utc)  # Wednesday


@pytest.fixture(autouse=True)
def clean_db():
    """Remove test DB before and after each test."""
    import pathlib
    p = pathlib.Path("/tmp/test_webhook.db")
    if p.exists():
        p.unlink()
    yield
    if p.exists():
        p.unlink()


@pytest.fixture
def client(clean_db):
    """Create a fresh TestClient per test (triggers lifespan with clean DB)."""
    with patch("src.krakenbot.main.datetime") as mock_dt:
        mock_dt.now.return_value = _FAKE_NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        with TestClient(app) as c:
            yield c


def _webhook(client, position: float, price: float = 87000.0, secret: str = "test-secret"):
    return client.post("/webhook", json={
        "action": "buy" if position > 0 else "sell",
        "order_id": "tv_123",
        "price": price,
        "qty": 1,
        "position": position,
        "comment": "test",
        "ticker": "BTCUSD",
        "time": "2026-03-28T12:00:00Z",
        "secret": secret,
    })


def test_health(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["position"] == "FLAT"


def test_invalid_secret(client):
    resp = _webhook(client, 1, secret="wrong")
    assert resp.status_code == 401


def test_open_long(client):
    resp = _webhook(client, 1)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "opened LONG" in data["action"]

    health = client.get("/").json()
    assert "LONG" in health["position"]


def test_close_long(client):
    _webhook(client, 1)  # open
    resp = _webhook(client, 0, price=88000.0)  # close
    assert resp.status_code == 200
    data = resp.json()
    assert "closed LONG" in data["action"]

    health = client.get("/").json()
    assert health["position"] == "FLAT"


def test_reverse_long_to_short(client):
    _webhook(client, 1, price=87000.0)  # open long
    resp = _webhook(client, -1, price=86000.0)  # reverse to short
    assert resp.status_code == 200
    data = resp.json()
    assert "reversed" in data["action"]

    health = client.get("/").json()
    assert "SHORT" in health["position"]


def test_duplicate_signal_noop(client):
    _webhook(client, 1)
    resp = _webhook(client, 1)  # same direction
    data = resp.json()
    assert data["action"] == "no_change"
