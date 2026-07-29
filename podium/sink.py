"""Async event bus. Everything downstream (gateway clients, state store, TUI feed)
subscribes to one sink; emitters never know who is listening.

Frames are plain dicts shaped by protocol.py. Subscribers get their own bounded queue;
a lagging subscriber is dropped past the cap (it reconnects and re-snapshots — LLD §18).
"""

import asyncio
from collections.abc import Callable

QUEUE_CAP = 4096


class Sink:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()
        self._taps: list[Callable[[dict], None]] = []

    def tap(self, fn: Callable[[dict], None]) -> None:
        """Synchronous observer (e.g. persistence). Must not block."""
        self._taps.append(fn)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_CAP)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def emit(self, frame: dict) -> None:
        for fn in self._taps:
            fn(frame)
        dead = []
        for q in self._queues:
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._queues.discard(q)

    __call__ = emit
