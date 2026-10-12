"""Unified Event Stream Manager with Circular Buffer and SSE Support."""

from __future__ import annotations

import collections
import json
import os
import threading
from typing import Dict, List, Optional

from hermes.platform.events.canonical_event import (
    CanonicalEvent,
    sign_event,
)


class EventStreamManager:
    """Manages circular event history, SSE publishing, and reconnection."""

    def __init__(self, max_buffer_size: int = 1000):
        self.max_buffer_size = max_buffer_size
        self._buffer: collections.deque[CanonicalEvent] = collections.deque(maxlen=max_buffer_size)
        self._lock = threading.Lock()
        self._session_sequences: Dict[str, int] = {}
        self._session_prev_hashes: Dict[str, str] = {}

    def get_signing_key(self) -> Optional[str]:
        return os.environ.get("HAOS_RECEIPT_KEY")

    def emit(
        self,
        session_id: str,
        event_type: str,
        payload: dict,
        signing_key: Optional[str] = None,
    ) -> CanonicalEvent:
        """Emit a new canonical event, binding it to the session chain."""
        key = signing_key or self.get_signing_key()
        with self._lock:
            next_seq = self._session_sequences.get(session_id, 0) + 1
            prev_h = self._session_prev_hashes.get(session_id, "0" * 64)

            event = CanonicalEvent(
                session_id=session_id,
                event_id=next_seq,
                event_type=event_type,
                payload=payload,
                prev_hash=prev_h,
            )

            if key:
                event.signature = sign_event(event.state_hash, key)

            self._session_sequences[session_id] = next_seq
            self._session_prev_hashes[session_id] = event.state_hash
            self._buffer.append(event)
            return event

    def get_events_after(
        self,
        last_event_id: int = 0,
        session_id: Optional[str] = None,
    ) -> List[CanonicalEvent]:
        """Return events occurring strictly after last_event_id (for SSE reconnection)."""
        with self._lock:
            results = []
            for ev in self._buffer:
                if ev.event_id > last_event_id:
                    if session_id is None or ev.session_id == session_id:
                        results.append(ev)
            return results

    def format_sse(self, event: CanonicalEvent) -> str:
        """Format event as standard Server-Sent Event text."""
        data_json = json.dumps(event.to_dict(), ensure_ascii=False)
        return f"id: {event.event_id}\nevent: {event.type}\ndata: {data_json}\n\n"

    def clear(self) -> None:
        """Clear memory buffer and sequence maps (useful in tests)."""
        with self._lock:
            self._buffer.clear()
            self._session_sequences.clear()
            self._session_prev_hashes.clear()


_GLOBAL_STREAM_MANAGER = EventStreamManager()


def get_event_stream_manager() -> EventStreamManager:
    return _GLOBAL_STREAM_MANAGER
