# Liquidation Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a real-time multi-exchange liquidation dashboard CLI that streams from Bybit and Binance WebSockets and displays in a rich TUI.

**Architecture:** Async WebSocket clients connect to Bybit (`allLiquidation`) and Binance (`forceOrder`) feeds. Events are normalized and stored in a rolling 24h deque. A Rich Live dashboard renders 5 panels every 500ms.

**Tech Stack:** Python 3.11+, asyncio, websockets, rich, python-dotenv

---

## Task 1: Project Setup & Dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `src/clients/__init__.py`
- Create: `src/ui/__init__.py`
- Create: `tests/__init__.py`

**Step 1: Update pyproject.toml with dependencies**

```toml
[project]
name = "liquidations"
version = "0.1.0"
description = "Real-time multi-exchange liquidation dashboard"
requires-python = ">=3.11"
dependencies = [
    "websockets>=12.0",
    "rich>=13.0",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[project.scripts]
liq-dashboard = "src.main:main"
```

**Step 2: Create package structure**

```bash
mkdir -p src/clients src/ui tests
touch src/__init__.py src/clients/__init__.py src/ui/__init__.py tests/__init__.py
```

**Step 3: Install dependencies**

```bash
uv sync
uv pip install -e ".[dev]"
```

**Step 4: Verify installation**

```bash
uv run python -c "import websockets, rich; print('OK')"
```
Expected: `OK`

**Step 5: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "chore: setup project structure and dependencies"
```

---

## Task 2: Data Model

**Files:**
- Create: `src/models.py`
- Create: `tests/test_models.py`

**Step 1: Write the failing test**

```python
# tests/test_models.py
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
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_models.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'src.models'`

**Step 3: Write minimal implementation**

```python
# src/models.py
from dataclasses import dataclass


@dataclass
class LiquidationEvent:
    exchange: str       # "bybit" | "binance"
    coin: str           # "BTC", "ETH", etc.
    side: str           # "long" | "short"
    size: float         # Position size
    price: float        # Liquidation price
    value_usd: float    # Total USD value
    timestamp: int      # Unix ms
    wallet: str | None  # Address if available
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_models.py -v
```
Expected: 2 passed

**Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: add LiquidationEvent data model"
```

---

## Task 3: Aggregator - Core Structure

**Files:**
- Create: `src/aggregator.py`
- Create: `tests/test_aggregator.py`

**Step 1: Write the failing tests**

```python
# tests/test_aggregator.py
import time
from src.models import LiquidationEvent
from src.aggregator import LiquidationAggregator


def make_event(
    coin="BTC",
    side="long",
    value_usd=10000.0,
    exchange="bybit",
    timestamp=None,
):
    return LiquidationEvent(
        exchange=exchange,
        coin=coin,
        side=side,
        size=value_usd / 90000,
        price=90000.0,
        value_usd=value_usd,
        timestamp=timestamp or int(time.time() * 1000),
        wallet=None,
    )


def test_add_event():
    agg = LiquidationAggregator()
    event = make_event()
    agg.add_event(event)
    assert len(agg.events) == 1


def test_filters_below_minimum():
    agg = LiquidationAggregator(min_value_usd=1000)
    small_event = make_event(value_usd=500)
    large_event = make_event(value_usd=5000)
    agg.add_event(small_event)
    agg.add_event(large_event)
    assert len(agg.events) == 1
    assert agg.events[0].value_usd == 5000


def test_prune_old_events():
    agg = LiquidationAggregator(window_ms=1000)  # 1 second window
    old_event = make_event(timestamp=int(time.time() * 1000) - 2000)
    new_event = make_event(timestamp=int(time.time() * 1000))
    agg.add_event(old_event)
    agg.add_event(new_event)
    agg._prune()
    assert len(agg.events) == 1
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_aggregator.py -v
```
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/aggregator.py
from collections import deque
import time
from src.models import LiquidationEvent

WINDOW_24H_MS = 24 * 60 * 60 * 1000
MIN_VALUE_USD = 1000


class LiquidationAggregator:
    def __init__(
        self,
        window_ms: int = WINDOW_24H_MS,
        min_value_usd: float = MIN_VALUE_USD,
    ):
        self.events: deque[LiquidationEvent] = deque()
        self.window_ms = window_ms
        self.min_value_usd = min_value_usd

    def add_event(self, event: LiquidationEvent) -> None:
        if event.value_usd < self.min_value_usd:
            return
        self.events.append(event)
        self._prune()

    def _prune(self) -> None:
        cutoff = int(time.time() * 1000) - self.window_ms
        while self.events and self.events[0].timestamp < cutoff:
            self.events.popleft()
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_aggregator.py -v
```
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/aggregator.py tests/test_aggregator.py
git commit -m "feat: add LiquidationAggregator with pruning"
```

---

## Task 4: Aggregator - Statistics Methods

**Files:**
- Modify: `src/aggregator.py`
- Modify: `tests/test_aggregator.py`

**Step 1: Write the failing tests**

Add to `tests/test_aggregator.py`:

```python
def test_top_10():
    agg = LiquidationAggregator(min_value_usd=0)
    for i in range(15):
        agg.add_event(make_event(value_usd=(i + 1) * 1000))
    top = agg.top_10()
    assert len(top) == 10
    assert top[0].value_usd == 15000
    assert top[9].value_usd == 6000


def test_by_coin():
    agg = LiquidationAggregator(min_value_usd=0)
    agg.add_event(make_event(coin="BTC", side="long", value_usd=10000))
    agg.add_event(make_event(coin="BTC", side="short", value_usd=5000))
    agg.add_event(make_event(coin="ETH", side="long", value_usd=3000))

    stats = agg.by_coin()
    assert stats["BTC"]["count"] == 2
    assert stats["BTC"]["total_usd"] == 15000
    assert stats["BTC"]["long_usd"] == 10000
    assert stats["BTC"]["short_usd"] == 5000
    assert stats["ETH"]["count"] == 1


def test_long_short_ratio():
    agg = LiquidationAggregator(min_value_usd=0)
    agg.add_event(make_event(side="long", value_usd=60000))
    agg.add_event(make_event(side="short", value_usd=40000))

    long_pct, short_pct = agg.long_short_ratio()
    assert long_pct == 60.0
    assert short_pct == 40.0


def test_by_exchange():
    agg = LiquidationAggregator(min_value_usd=0)
    agg.add_event(make_event(exchange="bybit", value_usd=10000))
    agg.add_event(make_event(exchange="binance", value_usd=5000))

    stats = agg.by_exchange()
    assert stats["bybit"]["count"] == 1
    assert stats["bybit"]["total_usd"] == 10000


def test_recent_feed():
    agg = LiquidationAggregator(min_value_usd=0)
    for i in range(20):
        agg.add_event(make_event(value_usd=(i + 1) * 100, timestamp=1700000000000 + i))

    recent = agg.recent_feed(limit=15)
    assert len(recent) == 15
    # Most recent first
    assert recent[0].timestamp == 1700000000019
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_aggregator.py -v
```
Expected: FAIL with `AttributeError: 'LiquidationAggregator' object has no attribute 'top_10'`

**Step 3: Add statistics methods to aggregator**

Add to `src/aggregator.py`:

```python
from collections import deque, defaultdict
import time
from typing import TypedDict
from src.models import LiquidationEvent

WINDOW_24H_MS = 24 * 60 * 60 * 1000
MIN_VALUE_USD = 1000


class CoinStats(TypedDict):
    count: int
    total_usd: float
    long_usd: float
    short_usd: float


class ExchangeStats(TypedDict):
    count: int
    total_usd: float


class LiquidationAggregator:
    def __init__(
        self,
        window_ms: int = WINDOW_24H_MS,
        min_value_usd: float = MIN_VALUE_USD,
    ):
        self.events: deque[LiquidationEvent] = deque()
        self.window_ms = window_ms
        self.min_value_usd = min_value_usd

    def add_event(self, event: LiquidationEvent) -> None:
        if event.value_usd < self.min_value_usd:
            return
        self.events.append(event)
        self._prune()

    def _prune(self) -> None:
        cutoff = int(time.time() * 1000) - self.window_ms
        while self.events and self.events[0].timestamp < cutoff:
            self.events.popleft()

    def top_10(self) -> list[LiquidationEvent]:
        return sorted(self.events, key=lambda e: e.value_usd, reverse=True)[:10]

    def by_coin(self) -> dict[str, CoinStats]:
        stats: dict[str, CoinStats] = defaultdict(
            lambda: {"count": 0, "total_usd": 0.0, "long_usd": 0.0, "short_usd": 0.0}
        )
        for e in self.events:
            s = stats[e.coin]
            s["count"] += 1
            s["total_usd"] += e.value_usd
            if e.side == "long":
                s["long_usd"] += e.value_usd
            else:
                s["short_usd"] += e.value_usd
        return dict(stats)

    def long_short_ratio(self) -> tuple[float, float]:
        long_total = sum(e.value_usd for e in self.events if e.side == "long")
        short_total = sum(e.value_usd for e in self.events if e.side == "short")
        total = long_total + short_total
        if total == 0:
            return 50.0, 50.0
        return round(long_total / total * 100, 1), round(short_total / total * 100, 1)

    def by_exchange(self) -> dict[str, ExchangeStats]:
        stats: dict[str, ExchangeStats] = defaultdict(
            lambda: {"count": 0, "total_usd": 0.0}
        )
        for e in self.events:
            s = stats[e.exchange]
            s["count"] += 1
            s["total_usd"] += e.value_usd
        return dict(stats)

    def recent_feed(self, limit: int = 15) -> list[LiquidationEvent]:
        return list(self.events)[-limit:][::-1]

    def total_24h(self) -> float:
        return sum(e.value_usd for e in self.events)
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_aggregator.py -v
```
Expected: 8 passed

**Step 5: Commit**

```bash
git add src/aggregator.py tests/test_aggregator.py
git commit -m "feat: add aggregator statistics methods"
```

---

## Task 5: Base WebSocket Client

**Files:**
- Create: `src/clients/base.py`
- Create: `tests/test_clients_base.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_clients_base.py -v
```
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/clients/base.py
from abc import ABC, abstractmethod
from typing import Callable
import asyncio
import logging

from src.models import LiquidationEvent

logger = logging.getLogger(__name__)


class BaseExchangeClient(ABC):
    exchange_name: str
    ws_url: str

    def __init__(
        self,
        coins: list[str],
        on_event: Callable[[LiquidationEvent], None],
    ):
        self.coins = coins
        self.on_event = on_event
        self.connected = False
        self._reconnect_delay = 1

    @abstractmethod
    def parse_message(self, data: dict) -> LiquidationEvent | None:
        """Parse exchange-specific message into LiquidationEvent."""
        pass

    @abstractmethod
    def get_subscribe_message(self) -> dict | list[dict]:
        """Return subscription message(s) for the WebSocket."""
        pass

    async def connect(self) -> None:
        """Connect to WebSocket with auto-reconnect."""
        import websockets

        while True:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.connected = True
                    self._reconnect_delay = 1
                    logger.info(f"{self.exchange_name}: Connected")

                    # Send subscription
                    sub_msg = self.get_subscribe_message()
                    if isinstance(sub_msg, list):
                        for msg in sub_msg:
                            await ws.send(__import__("json").dumps(msg))
                    else:
                        await ws.send(__import__("json").dumps(sub_msg))

                    # Listen for messages
                    async for raw_msg in ws:
                        await self._handle_message(raw_msg)

            except Exception as e:
                self.connected = False
                logger.warning(
                    f"{self.exchange_name}: Disconnected ({e}), "
                    f"reconnecting in {self._reconnect_delay}s"
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 30)

    async def _handle_message(self, raw_msg: str) -> None:
        import json

        try:
            data = json.loads(raw_msg)
            event = self.parse_message(data)
            if event:
                self.on_event(event)
        except Exception as e:
            logger.debug(f"{self.exchange_name}: Parse error: {e}")
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_clients_base.py -v
```
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/clients/base.py tests/test_clients_base.py
git commit -m "feat: add BaseExchangeClient with reconnect logic"
```

---

## Task 6: Bybit Client

**Files:**
- Create: `src/clients/bybit.py`
- Create: `tests/test_clients_bybit.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_clients_bybit.py -v
```
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/clients/bybit.py
from src.clients.base import BaseExchangeClient
from src.models import LiquidationEvent


class BybitClient(BaseExchangeClient):
    exchange_name = "bybit"
    ws_url = "wss://stream.bybit.com/v5/public/linear"

    def get_subscribe_message(self) -> dict:
        args = [f"allLiquidation.{coin}USDT" for coin in self.coins]
        return {"op": "subscribe", "args": args}

    def parse_message(self, data: dict) -> LiquidationEvent | None:
        # Check if this is a liquidation message
        topic = data.get("topic", "")
        if not topic.startswith("allLiquidation."):
            return None

        liq_data = data.get("data")
        if not liq_data:
            return None

        # Extract coin from symbol (e.g., "BTCUSDT" -> "BTC")
        symbol = liq_data.get("s", "")
        coin = symbol.replace("USDT", "").replace("PERP", "")

        # "Buy" means a long was liquidated, "Sell" means a short was liquidated
        side = "long" if liq_data.get("S") == "Buy" else "short"

        size = float(liq_data.get("v", 0))
        price = float(liq_data.get("p", 0))
        value_usd = size * price

        return LiquidationEvent(
            exchange=self.exchange_name,
            coin=coin,
            side=side,
            size=size,
            price=price,
            value_usd=value_usd,
            timestamp=liq_data.get("T", data.get("ts", 0)),
            wallet=None,  # Bybit doesn't provide wallet
        )
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_clients_bybit.py -v
```
Expected: 5 passed

**Step 5: Commit**

```bash
git add src/clients/bybit.py tests/test_clients_bybit.py
git commit -m "feat: add Bybit WebSocket client"
```

---

## Task 7: Binance Client

**Files:**
- Create: `src/clients/binance.py`
- Create: `tests/test_clients_binance.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_clients_binance.py -v
```
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/clients/binance.py
from src.clients.base import BaseExchangeClient
from src.models import LiquidationEvent


class BinanceClient(BaseExchangeClient):
    exchange_name = "binance"

    def __init__(self, coins: list[str], on_event):
        super().__init__(coins, on_event)
        # Binance uses combined stream URL
        streams = "/".join(f"{coin.lower()}usdt@forceOrder" for coin in coins)
        self.ws_url = f"wss://fstream.binance.com/stream?streams={streams}"

    def get_subscribe_message(self) -> dict:
        # Binance combined stream doesn't need subscription message
        return {"method": "REQUEST", "params": [], "id": 1}

    def parse_message(self, data: dict) -> LiquidationEvent | None:
        # Check if this is a forceOrder message
        if "data" not in data or data.get("data", {}).get("e") != "forceOrder":
            return None

        order = data["data"].get("o", {})
        if not order:
            return None

        # Extract coin from symbol
        symbol = order.get("s", "")
        coin = symbol.replace("USDT", "").replace("PERP", "")

        # "SELL" means a long was liquidated, "BUY" means a short was liquidated
        side = "long" if order.get("S") == "SELL" else "short"

        size = float(order.get("z", order.get("q", 0)))  # Filled qty or original qty
        price = float(order.get("ap", order.get("p", 0)))  # Avg price or order price
        value_usd = size * price

        return LiquidationEvent(
            exchange=self.exchange_name,
            coin=coin,
            side=side,
            size=size,
            price=price,
            value_usd=value_usd,
            timestamp=order.get("T", data["data"].get("E", 0)),
            wallet=None,  # Binance doesn't provide wallet
        )
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_clients_binance.py -v
```
Expected: 5 passed

**Step 5: Commit**

```bash
git add src/clients/binance.py tests/test_clients_binance.py
git commit -m "feat: add Binance WebSocket client"
```

---

## Task 8: Dashboard - Helper Functions

**Files:**
- Create: `src/ui/dashboard.py`
- Create: `tests/test_ui_dashboard.py`

**Step 1: Write the failing tests**

```python
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
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_ui_dashboard.py -v
```
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/ui/dashboard.py
from datetime import datetime


def format_usd(value: float) -> str:
    """Format USD value with K/M suffix."""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.0f}"


def format_time(timestamp_ms: int) -> str:
    """Format timestamp to HH:MM:SS."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    return dt.strftime("%H:%M:%S")


def truncate_wallet(wallet: str | None, length: int = 6) -> str:
    """Truncate wallet address for display."""
    if not wallet:
        return "-"
    if len(wallet) <= length + 3:
        return wallet
    return f"{wallet[:length]}..."
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_ui_dashboard.py -v
```
Expected: 5 passed

**Step 5: Commit**

```bash
git add src/ui/dashboard.py tests/test_ui_dashboard.py
git commit -m "feat: add dashboard helper functions"
```

---

## Task 9: Dashboard - Panel Builders

**Files:**
- Modify: `src/ui/dashboard.py`

**Step 1: Add imports and panel builder functions**

Add to `src/ui/dashboard.py`:

```python
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.align import Align

from src.aggregator import LiquidationAggregator
from src.models import LiquidationEvent


def format_usd(value: float) -> str:
    """Format USD value with K/M suffix."""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.0f}"


def format_time(timestamp_ms: int) -> str:
    """Format timestamp to HH:MM:SS."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    return dt.strftime("%H:%M:%S")


def truncate_wallet(wallet: str | None, length: int = 6) -> str:
    """Truncate wallet address for display."""
    if not wallet:
        return "-"
    if len(wallet) <= length + 3:
        return wallet
    return f"{wallet[:length]}..."


def build_ratio_bar(long_pct: float, short_pct: float, width: int = 50) -> Text:
    """Build the long/short ratio bar."""
    long_chars = int(width * long_pct / 100)
    short_chars = width - long_chars

    bar = Text()
    bar.append(" LONGS ", style="bold white on green")
    bar.append(" " + "█" * long_chars, style="green")
    bar.append("░" * short_chars + " ", style="red")
    bar.append(" SHORTS ", style="bold white on red")
    bar.append(f"  ({long_pct:.0f}% / {short_pct:.0f}%)", style="dim")
    return bar


def build_top10_table(events: list[LiquidationEvent]) -> Table:
    """Build the top 10 liquidations table."""
    table = Table(title="TOP 10 LARGEST LIQUIDATIONS (24H)", expand=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Value", style="bold")
    table.add_column("Coin")
    table.add_column("Side")
    table.add_column("Price", justify="right")
    table.add_column("Wallet")
    table.add_column("Time")

    for i, event in enumerate(events, 1):
        side_style = "green" if event.side == "long" else "red"
        value_style = "bold yellow" if event.value_usd > 100_000 else "bold"

        table.add_row(
            str(i),
            Text(format_usd(event.value_usd), style=value_style),
            event.coin,
            Text(event.side.upper(), style=side_style),
            f"${event.price:,.2f}",
            truncate_wallet(event.wallet),
            format_time(event.timestamp),
        )
    return table


def build_coin_table(aggregator: LiquidationAggregator) -> Table:
    """Build the liquidations by coin table."""
    table = Table(title="LIQUIDATIONS BY COIN (24H)", expand=True)
    table.add_column("Coin")
    table.add_column("Count", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Long $", justify="right", style="green")
    table.add_column("Short $", justify="right", style="red")
    table.add_column("Exchange", justify="right")

    by_coin = aggregator.by_coin()
    by_exchange = aggregator.by_exchange()

    # Sort by total value descending
    sorted_coins = sorted(by_coin.items(), key=lambda x: x[1]["total_usd"], reverse=True)

    for coin, stats in sorted_coins[:10]:
        # Calculate exchange breakdown for this coin
        coin_events = [e for e in aggregator.events if e.coin == coin]
        bybit_count = sum(1 for e in coin_events if e.exchange == "bybit")
        binance_count = sum(1 for e in coin_events if e.exchange == "binance")
        total_count = bybit_count + binance_count

        if total_count > 0:
            exchange_str = f"BB:{bybit_count * 100 // total_count}% BN:{binance_count * 100 // total_count}%"
        else:
            exchange_str = "-"

        table.add_row(
            coin,
            f"{stats['count']:,}",
            format_usd(stats["total_usd"]),
            format_usd(stats["long_usd"]),
            format_usd(stats["short_usd"]),
            exchange_str,
        )
    return table


def build_live_feed(events: list[LiquidationEvent]) -> Table:
    """Build the live feed table."""
    table = Table(title="LIVE FEED", expand=True, show_header=False)
    table.add_column("Time", width=10)
    table.add_column("Exchange", width=8)
    table.add_column("Coin", width=6)
    table.add_column("Side", width=6)
    table.add_column("Value", width=10)
    table.add_column("Price", width=12)

    for event in events:
        side_style = "green" if event.side == "long" else "red"
        exchange_style = "yellow" if event.exchange == "bybit" else "cyan"

        table.add_row(
            format_time(event.timestamp),
            Text(event.exchange.upper(), style=exchange_style),
            event.coin,
            Text(event.side.upper(), style=side_style),
            format_usd(event.value_usd),
            f"${event.price:,.2f}",
        )
    return table


def build_status_bar(
    bybit_connected: bool,
    binance_connected: bool,
    total_24h: float,
) -> Text:
    """Build the status bar."""
    status = Text()

    # Connection status
    status.append(" Connected: ")
    status.append("BYBIT ", style="yellow")
    status.append("● " if bybit_connected else "○ ", style="green" if bybit_connected else "red")
    status.append("BINANCE ", style="cyan")
    status.append("● " if binance_connected else "○ ", style="green" if binance_connected else "red")

    status.append(" │ ", style="dim")
    status.append(f"Total 24H: {format_usd(total_24h)}", style="bold")
    status.append(" │ ", style="dim")
    status.append("Ctrl+C exit", style="dim")

    return status
```

**Step 2: Run all tests**

```bash
uv run pytest tests/ -v
```
Expected: All tests pass

**Step 3: Commit**

```bash
git add src/ui/dashboard.py
git commit -m "feat: add dashboard panel builders"
```

---

## Task 10: Dashboard - Main Renderer

**Files:**
- Modify: `src/ui/dashboard.py`

**Step 1: Add the Dashboard class**

Add to bottom of `src/ui/dashboard.py`:

```python
from rich.live import Live
from rich.console import Group


class Dashboard:
    def __init__(self, aggregator: LiquidationAggregator):
        self.aggregator = aggregator
        self.bybit_connected = False
        self.binance_connected = False
        self.console = Console()

    def set_connection_status(self, exchange: str, connected: bool) -> None:
        if exchange == "bybit":
            self.bybit_connected = connected
        elif exchange == "binance":
            self.binance_connected = connected

    def render(self) -> Group:
        """Render the full dashboard."""
        long_pct, short_pct = self.aggregator.long_short_ratio()

        return Group(
            Align.center(build_ratio_bar(long_pct, short_pct)),
            "",
            build_top10_table(self.aggregator.top_10()),
            "",
            build_coin_table(self.aggregator),
            "",
            build_live_feed(self.aggregator.recent_feed(15)),
            "",
            Panel(build_status_bar(
                self.bybit_connected,
                self.binance_connected,
                self.aggregator.total_24h(),
            ), style="dim"),
        )

    def create_live(self) -> Live:
        """Create a Rich Live display."""
        return Live(
            self.render(),
            console=self.console,
            refresh_per_second=2,
            screen=True,
        )
```

**Step 2: Run all tests**

```bash
uv run pytest tests/ -v
```
Expected: All tests pass

**Step 3: Commit**

```bash
git add src/ui/dashboard.py
git commit -m "feat: add Dashboard renderer class"
```

---

## Task 11: Main Entry Point

**Files:**
- Create: `src/main.py`

**Step 1: Create the main entry point**

```python
# src/main.py
import asyncio
import logging
import signal
from src.aggregator import LiquidationAggregator
from src.clients.bybit import BybitClient
from src.clients.binance import BinanceClient
from src.ui.dashboard import Dashboard

# Top coins by volume
COINS = [
    "BTC", "ETH", "SOL", "XRP", "DOGE",
    "ADA", "AVAX", "LINK", "DOT", "MATIC",
    "UNI", "LTC", "BCH", "ATOM", "APT",
]

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def run_dashboard():
    """Main async entry point."""
    aggregator = LiquidationAggregator()
    dashboard = Dashboard(aggregator)

    def on_event(event):
        aggregator.add_event(event)

    # Create clients
    bybit = BybitClient(coins=COINS, on_event=on_event)
    binance = BinanceClient(coins=COINS, on_event=on_event)

    # Track connection status
    original_bybit_connect = bybit.connect
    original_binance_connect = binance.connect

    async def bybit_connect_wrapper():
        try:
            dashboard.set_connection_status("bybit", True)
            await original_bybit_connect()
        except Exception:
            dashboard.set_connection_status("bybit", False)
            raise

    async def binance_connect_wrapper():
        try:
            dashboard.set_connection_status("binance", True)
            await original_binance_connect()
        except Exception:
            dashboard.set_connection_status("binance", False)
            raise

    bybit.connect = bybit_connect_wrapper
    binance.connect = binance_connect_wrapper

    # Start WebSocket tasks
    ws_tasks = [
        asyncio.create_task(bybit.connect()),
        asyncio.create_task(binance.connect()),
    ]

    # Run dashboard
    try:
        with dashboard.create_live() as live:
            while True:
                live.update(dashboard.render())
                await asyncio.sleep(0.5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for task in ws_tasks:
            task.cancel()


def main():
    """Sync entry point."""
    try:
        asyncio.run(run_dashboard())
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
```

**Step 2: Test manually**

```bash
uv run python -m src.main
```
Expected: Dashboard displays and connects to exchanges (Ctrl+C to exit)

**Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: add main entry point"
```

---

## Task 12: Update Client Exports

**Files:**
- Modify: `src/clients/__init__.py`

**Step 1: Add exports**

```python
# src/clients/__init__.py
from src.clients.bybit import BybitClient
from src.clients.binance import BinanceClient

__all__ = ["BybitClient", "BinanceClient"]
```

**Step 2: Commit**

```bash
git add src/clients/__init__.py
git commit -m "chore: export clients from package"
```

---

## Task 13: Final Integration Test

**Step 1: Run all tests**

```bash
uv run pytest tests/ -v
```
Expected: All tests pass

**Step 2: Run the dashboard**

```bash
uv run python -m src.main
```
Expected:
- Dashboard displays with all 5 panels
- Connection indicators show green for both exchanges
- Liquidations stream in real-time
- Ctrl+C exits cleanly

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: complete liquidation dashboard v1.0"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Project setup | pyproject.toml, src/, tests/ |
| 2 | Data model | src/models.py |
| 3 | Aggregator core | src/aggregator.py |
| 4 | Aggregator stats | src/aggregator.py |
| 5 | Base WS client | src/clients/base.py |
| 6 | Bybit client | src/clients/bybit.py |
| 7 | Binance client | src/clients/binance.py |
| 8 | Dashboard helpers | src/ui/dashboard.py |
| 9 | Dashboard panels | src/ui/dashboard.py |
| 10 | Dashboard renderer | src/ui/dashboard.py |
| 11 | Main entry | src/main.py |
| 12 | Package exports | src/clients/__init__.py |
| 13 | Integration test | - |
