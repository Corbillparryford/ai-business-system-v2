"""
trading/ws_broadcaster.py
=========================
WebSocket server for the React dashboard. Port 8765.
"""

import asyncio
import json
import logging

import websockets
from websockets.exceptions import ConnectionClosed

from core.db import get_active_trading_signals

log = logging.getLogger(__name__)

_connected: set = set()
_queue: asyncio.Queue = asyncio.Queue(maxsize=500)


def get_queue() -> asyncio.Queue:
    return _queue


async def _handler(ws):
    _connected.add(ws)
    log.info("WS client connected. Total: %d", len(_connected))
    try:
        await ws.send(json.dumps({
            "type":           "INIT",
            "active_signals": get_active_trading_signals(),
        }))
        await ws.wait_closed()
    except ConnectionClosed:
        pass
    finally:
        _connected.discard(ws)
        log.info("WS client disconnected. Total: %d", len(_connected))


async def _broadcast_worker():
    while True:
        msg = await _queue.get()
        if _connected:
            payload = json.dumps(msg)
            await asyncio.gather(
                *[c.send(payload) for c in list(_connected)],
                return_exceptions=True,
            )
        _queue.task_done()


async def _start():
    async with websockets.serve(_handler, "0.0.0.0", 8765):
        log.info("WebSocket server on :8765")
        await _broadcast_worker()


def run_ws_server():
    asyncio.run(_start())
