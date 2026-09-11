"""In-memory pub/sub for run events (drives the live council UI over SSE).

Each run keeps a sequential event history (also persisted into the Run row) so
that late-joining SSE clients can replay everything that happened.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._queues: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, run_id: str, event_type: str, **data: Any) -> dict[str, Any]:
        event = {"seq": len(self._history.setdefault(run_id, [])) + 1,
                 "ts": time.time(), "type": event_type, **data}
        async with self._lock:
            self._history.setdefault(run_id, []).append(event)
            for q in list(self._queues.get(run_id, [])):
                q.put_nowait(event)
        return event

    async def subscribe(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        history = list(self._history.get(run_id, []))
        for evt in history:
            yield evt
        self._queues.setdefault(run_id, []).append(queue)
        try:
            while True:
                evt = await queue.get()
                yield evt
                if evt.get("type") in ("run_finished", "run_failed"):
                    break
        finally:
            if queue in self._queues.get(run_id, []):
                self._queues[run_id].remove(queue)

    def history(self, run_id: str) -> list[dict[str, Any]]:
        return list(self._history.get(run_id, []))

    def cleanup(self, run_id: str) -> None:
        self._history.pop(run_id, None)
        self._queues.pop(run_id, None)


bus = EventBus()
