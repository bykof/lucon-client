"""Unsolicited device events: SSE stream + replay (ADR-0004)."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from lucon_api.deps import HubDep
from lucon_api.events import Event
from lucon_api.schemas import EventOut

router = APIRouter(tags=["events"])

# Heartbeat cadence so dead SSE connections are noticed and proxies stay warm.
_KEEPALIVE_S = 15.0


def _format_sse(event: Event) -> str:
    data = json.dumps(
        {
            "id": event["id"],
            "ts": event["ts"],
            "kind": event["kind"],
            "message": event["message"],
            "raw": event["raw"],
        }
    )
    return f"id: {event['id']}\nevent: {event['kind']}\ndata: {data}\n\n"


@router.get("/events")
async def stream_events(
    request: Request,
    hub: HubDep,
    last_event_id: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    """Subscribe to the device notice stream (text/event-stream), with replay.

    Replays buffered notices after the given id — taken from ``?last_event_id``
    or the standard ``Last-Event-ID`` reconnection header.
    """
    after = last_event_id
    if after is None:
        header = request.headers.get("last-event-id", "")
        after = int(header) if header.isdigit() else 0

    queue = hub.register(after_id=after)

    async def generate() -> AsyncIterator[str]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _format_sse(event)
        finally:
            hub.unregister(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/events/recent", response_model=list[EventOut])
def recent_events(
    hub: HubDep,
    after_id: Annotated[int, Query(ge=0)] = 0,
) -> list[EventOut]:
    """The buffered notices with id greater than ``after_id`` (replay window)."""
    return [EventOut(**event) for event in hub.recent(after_id)]
