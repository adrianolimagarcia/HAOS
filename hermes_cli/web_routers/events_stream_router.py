"""FastAPI Router for Canonical Event Streaming (SSE).

Supports:
- GET /stream/events
- GET /api/events/stream
- Last-Event-ID HTTP header and query param reconnection
"""

from __future__ import annotations

import asyncio
from typing import Optional
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from hermes.platform.events.event_stream import (
    EventStreamManager,
    get_event_stream_manager,
)

router = APIRouter(tags=["events-stream"])


async def _event_generator(
    manager: EventStreamManager,
    last_event_id: int,
    session_id: Optional[str] = None,
):
    # First deliver past missed events
    backlog = manager.get_events_after(last_event_id=last_event_id, session_id=session_id)
    highest_id = last_event_id
    for ev in backlog:
        if ev.event_id > highest_id:
            highest_id = ev.event_id
        yield manager.format_sse(ev)

    # Yield heartbeat or brief stream
    yield ": ping\n\n"


@router.get("/stream/events")
@router.get("/api/events/stream")
async def stream_events(
    request: Request,
    last_event_id_header: Optional[str] = Header(None, alias="Last-Event-ID"),
    last_event_id_query: Optional[int] = Query(None, alias="last_event_id"),
    session_id: Optional[str] = Query(None),
):
    manager = get_event_stream_manager()
    parsed_last_id = 0
    if last_event_id_query is not None:
        parsed_last_id = last_event_id_query
    elif last_event_id_header:
        try:
            parsed_last_id = int(last_event_id_header)
        except ValueError:
            pass

    return StreamingResponse(
        _event_generator(manager, parsed_last_id, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
