# Hyperliquid Liquidation Dashboard (Python Backend)

This service ingests real-time liquidation events from Hyperliquid, maintains a rolling 24-hour dataset, and exposes APIs for a live dashboard.

---

## Overview

The backend will:

1. Connect to Hyperliquid WebSocket feeds
2. Stream real-time liquidation events
3. Maintain a rolling 24h in-memory window
4. Provide REST endpoints for dashboard panels
5. Provide a WebSocket endpoint for live UI updates

---

## Tech Stack

- Python 3.11+
- FastAPI
- Uvicorn
- websockets
- Pydantic
- asyncio
- collections.deque

Install dependencies:

```bash
pip install fastapi uvicorn websockets pydantic python-dateutil
```

---

## Project Structure

```
hyperliquid_liq_dashboard/
│
├── app.py
├── hl_client.py
├── aggregator.py
├── models.py
└── requirements.txt
```

---

## Data Model

```python
# models.py
from pydantic import BaseModel
from typing import Literal

class LiquidationEvent(BaseModel):
    coin: str
    side: Literal["long", "short"]
    price: float
    size: float
    value_usd: float
    account: str
    timestamp: int  # milliseconds
```

---

## Rolling 24h Aggregation Engine

```python
# aggregator.py
from collections import deque, defaultdict
import time

WINDOW_MS = 24 * 60 * 60 * 1000

class LiquidationAggregator:
    def __init__(self):
        self.events = deque()

    def add_event(self, event):
        self.events.append(event)
        self._prune()

    def _prune(self):
        cutoff = int(time.time() * 1000) - WINDOW_MS
        while self.events and self.events[0].timestamp < cutoff:
            self.events.popleft()

    def top_10(self):
        return sorted(self.events, key=lambda e: e.value_usd, reverse=True)[:10]

    def summary_by_coin(self):
        summary = defaultdict(lambda: {
            "count": 0,
            "total_usd": 0,
            "long_usd": 0,
            "short_usd": 0
        })

        for e in self.events:
            s = summary[e.coin]
            s["count"] += 1
            s["total_usd"] += e.value_usd
            if e.side == "long":
                s["long_usd"] += e.value_usd
            else:
                s["short_usd"] += e.value_usd

        return summary
```

---

## Hyperliquid WebSocket Client

```python
# hl_client.py
import json
import websockets
from models import LiquidationEvent

HL_WS_URL = "wss://api.hyperliquid.xyz/ws"

class HyperliquidClient:
    def __init__(self, aggregator):
        self.aggregator = aggregator

    async def connect(self):
        async with websockets.connect(HL_WS_URL) as ws:
            await self.subscribe(ws)
            async for msg in ws:
                await self.handle_message(msg)

    async def subscribe(self, ws):
        sub_msg = {
            "method": "subscribe",
            "subscription": {"type": "liquidations"}
        }
        await ws.send(json.dumps(sub_msg))

    async def handle_message(self, msg):
        data = json.loads(msg)

        if "liquidation" not in data:
            return

        liq = data["liquidation"]

        event = LiquidationEvent(
            coin=liq["coin"],
            side=liq["side"],
            price=float(liq["price"]),
            size=float(liq["size"]),
            value_usd=float(liq["valueUsd"]),
            account=liq["account"],
            timestamp=int(liq["timestamp"])
        )

        self.aggregator.add_event(event)
```

---

## FastAPI Server

```python
# app.py
import asyncio
from fastapi import FastAPI, WebSocket
from aggregator import LiquidationAggregator
from hl_client import HyperliquidClient

app = FastAPI()
aggregator = LiquidationAggregator()

@app.on_event("startup")
async def startup_event():
    client = HyperliquidClient(aggregator)
    asyncio.create_task(client.connect())

@app.get("/api/top10")
def get_top10():
    return [e.dict() for e in aggregator.top_10()]

@app.get("/api/summary")
def get_summary():
    return aggregator.summary_by_coin()

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    while True:
        await asyncio.sleep(1)
        await ws.send_json({
            "top10": [e.dict() for e in aggregator.top_10()],
            "summary": aggregator.summary_by_coin()
        })
```

Run server:

```bash
uvicorn app:app --reload
```

---

## API Outputs

### GET /api/top10

```json
[
  {
    "coin": "BTC",
    "side": "long",
    "price": 90268,
    "size": 4.9,
    "value_usd": 442900,
    "account": "0xabc...",
    "timestamp": 1700000000000
  }
]
```

### GET /api/summary

```json
{
  "BTC": {
    "count": 27444,
    "total_usd": 71800000,
    "long_usd": 42700000,
    "short_usd": 29100000
  }
}
```

---c

## Scaling & Production Notes

- Use Redis instead of memory for horizontal scaling
- Store history in TimescaleDB for charts
- Batch outbound WS updates every 250ms
- Add historical endpoints for charts
- Add health check endpoint for orchestration