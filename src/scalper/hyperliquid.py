import asyncio
import json
import logging
import websockets

logger = logging.getLogger(__name__)


class HyperliquidPriceFeed:
    def __init__(self, coin: str = "BTC"):
        self.coin = coin
        self.ws_url = "wss://api.hyperliquid.xyz/ws"
        self.current_price: float = 0.0
        self.connected: bool = False
        self._on_price: callable | None = None  # optional callback

    def set_on_price(self, callback):
        """Set callback for price updates: callback(price: float)"""
        self._on_price = callback

    async def connect(self) -> None:
        """Connect to WebSocket and stream prices. Auto-reconnects."""
        while True:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.connected = True
                    # Subscribe to allMids
                    sub_msg = {"method": "subscribe", "subscription": {"type": "allMids"}}
                    await ws.send(json.dumps(sub_msg))

                    async for msg in ws:
                        data = json.loads(msg)
                        self._handle_message(data)
            except Exception as e:
                self.connected = False
                logger.warning(f"HyperLiquid disconnected: {e}, reconnecting in 2s")
                await asyncio.sleep(2)

    def _handle_message(self, data: dict) -> None:
        """Parse price from allMids message."""
        if data.get("channel") == "allMids":
            mids = data.get("data", {}).get("mids", {})
            price_str = mids.get(self.coin)
            if price_str:
                self.current_price = float(price_str)
                if self._on_price:
                    self._on_price(self.current_price)
