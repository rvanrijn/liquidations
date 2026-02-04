# src/orderflow/clients/base.py
from abc import ABC, abstractmethod
from typing import Callable
import asyncio
import logging

from src.orderflow.models import TradeEvent

logger = logging.getLogger(__name__)


class BaseTradeClient(ABC):
    exchange_name: str
    ws_url: str

    def __init__(
        self,
        coins: list[str],
        on_event: Callable[[TradeEvent], None],
    ):
        self.coins = coins
        self.on_event = on_event
        self.connected = False
        self._reconnect_delay = 1

    @abstractmethod
    def parse_message(self, data: dict) -> TradeEvent | None:
        """Parse exchange-specific message into TradeEvent."""
        pass

    @abstractmethod
    def get_subscribe_message(self) -> dict | list[dict] | None:
        """Return subscription message(s) for the WebSocket, or None if not needed."""
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

                    # Send subscription if needed
                    sub_msg = self.get_subscribe_message()
                    if sub_msg is not None:
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
