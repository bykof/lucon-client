"""In-process pub/sub fan-out for unsolicited device notices (ADR-0004).

``lucon``'s ``poll_events`` queue is single-consumer, so multiple HTTP clients
cannot each poll it without stealing one another's notices. The gateway is the
sole consumer: its ``on_event``/``on_error`` callbacks feed :class:`EventHub`,
which fans every notice out to all SSE subscribers and keeps a bounded ring
buffer for ``Last-Event-ID`` replay.

Callbacks fire on ``lucon``'s RX thread (not the event loop), so publishing
hops onto the loop via ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any

_LOG = logging.getLogger("lucon_api.events")

Event = dict[str, Any]


class EventHub:
    """Thread-safe fan-out of device notices to async SSE subscribers."""

    def __init__(self, buffer_size: int) -> None:
        self._lock = threading.Lock()
        self._buffer: deque[Event] = deque(maxlen=buffer_size)
        # Per-subscriber queues are bounded so a stalled SSE client can't grow
        # memory without limit; on overflow we drop the oldest (keep recent).
        self._queue_maxsize = max(buffer_size * 2, 16)
        self._next_id = 1
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the serving event loop (called once in the app lifespan)."""
        with self._lock:
            self._loop = loop

    def publish(self, *, kind: str, message: str | None, raw: str | None) -> Event:
        """Append a notice to the ring buffer and fan it out. Called off-loop.

        Returns the stored event (with its assigned ``id``/``ts``).
        """
        with self._lock:
            event: Event = {
                "id": self._next_id,
                "ts": time.time(),
                "kind": kind,
                "message": message,
                "raw": raw,
            }
            self._next_id += 1
            self._buffer.append(event)
            loop = self._loop
            subscribers = list(self._subscribers)
        if loop is not None:
            for queue in subscribers:
                try:
                    loop.call_soon_threadsafe(self._deliver, queue, event)
                except RuntimeError:
                    # Loop is closing/closed (shutdown); drop the live delivery.
                    _LOG.debug("event loop unavailable; dropping live delivery")
        return event

    @staticmethod
    def _deliver(queue: "asyncio.Queue[Event]", event: Event) -> None:
        """Enqueue on the loop thread; evict the oldest if the queue is full."""
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - racing consumer
                pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - racing producers
            pass

    def recent(self, after_id: int = 0) -> list[Event]:
        """Snapshot buffered notices with ``id`` greater than ``after_id``."""
        with self._lock:
            return [event for event in self._buffer if event["id"] > after_id]

    def register(self, after_id: int = 0) -> asyncio.Queue[Event]:
        """Subscribe a fresh queue, pre-loaded with any missed backlog.

        Backlog is snapshotted under the same lock that adds the subscriber, so
        a concurrent :meth:`publish` is delivered either via the backlog or live
        — never both, never neither.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_maxsize)
        with self._lock:
            backlog = [event for event in self._buffer if event["id"] > after_id]
            self._subscribers.add(queue)
        for event in backlog:
            queue.put_nowait(event)
        return queue

    def unregister(self, queue: asyncio.Queue[Event]) -> None:
        """Drop a subscriber (on SSE disconnect)."""
        with self._lock:
            self._subscribers.discard(queue)
