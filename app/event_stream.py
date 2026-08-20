from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


class EventBroker:
    """Small in-process pub/sub broker for one FastAPI instance.

    Repository writes are synchronous and can happen outside the event-loop
    thread. ``publish`` therefore schedules delivery thread-safely and never
    blocks the production worker.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = threading.RLock()
        self._sequence = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, entity: str, payload: dict[str, Any]) -> None:
        loop = self._loop
        if not loop or loop.is_closed():
            return
        event = {
            "entity": entity,
            "payload": payload,
        }
        loop.call_soon_threadsafe(self._deliver, event)

    def _deliver(self, event: dict[str, Any]) -> None:
        self._sequence += 1
        message = {"id": self._sequence, **event}
        with self._lock:
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(message)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            with self._lock:
                self._subscribers.discard(queue)

    @staticmethod
    def encode_sse(event: dict[str, Any]) -> str:
        data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        return f"id: {event.get('id', '')}\ndata: {data}\n\n"
