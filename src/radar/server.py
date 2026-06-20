"""FastAPI transport for the cascade radar. Serves static UI + /ws broadcast."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.radar.runner import RadarRunner

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


class ConnectionManager:
    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop):
        self._loop = loop

    async def connect(self, ws: WebSocket):
        await ws.accept()
        # Capture the running loop on first connection so broadcast_sync works
        # even when the startup event did not fire (e.g. WebSocketTestSession).
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket):
        self._clients.discard(ws)

    async def broadcast(self, message: dict):
        dead = []
        payload = json.dumps(message)
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_sync(self, message: dict):
        """Thread/callback-safe entry from the runner's on_liq callback."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), self._loop)


def make_app(runner: RadarRunner, start_loop: bool = True) -> FastAPI:
    app = FastAPI(title="Liqhunt Cascade Radar")
    manager = ConnectionManager()
    app.state.manager = manager
    app.state.runner = runner

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await manager.connect(ws)
        try:
            if runner.state is not None:
                await ws.send_text(json.dumps(runner.state.to_dict()))
            while True:
                await ws.receive_text()  # ignore client input; keep alive
        except WebSocketDisconnect:
            manager.disconnect(ws)
        except Exception:
            manager.disconnect(ws)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.on_event("startup")
    async def _startup():
        manager.bind_loop(asyncio.get_running_loop())
        runner.on_liq = manager.broadcast_sync
        if start_loop:
            asyncio.create_task(runner.run())
            asyncio.create_task(_state_broadcast_loop())

    async def _state_broadcast_loop():
        while True:
            if runner.state is not None:
                await manager.broadcast(runner.state.to_dict())
            await asyncio.sleep(0.5)  # ~2 Hz

    return app


# Module-level app for `uvicorn src.radar.server:app`
app = make_app(RadarRunner())
