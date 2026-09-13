"""Knowledge Events & Event Bus for Memory subsystem.

Defines knowledge-related event types, data structures, and an event bus
supporting publication, callback subscriptions, and queueing for background workers.
Strictly stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional


class KnowledgeEventType(Enum):
    NOTE_CREATED = "NOTE_CREATED"
    NOTE_MODIFIED = "NOTE_MODIFIED"
    NOTE_DELETED = "NOTE_DELETED"
    DECISION_RECORDED = "DECISION_RECORDED"


@dataclass
class KnowledgeEvent:
    event_id: str
    event_type: KnowledgeEventType
    uri: str
    title: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        event_type: KnowledgeEventType,
        uri: str,
        title: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> KnowledgeEvent:
        return cls(
            event_id=event_id or str(uuid.uuid4()),
            event_type=event_type,
            uri=uri,
            title=title,
            content=content,
            metadata=dict(metadata or {}),
            timestamp=timestamp if timestamp is not None else time.time(),
        )


class KnowledgeEventBus:
    """Publish-subscribe event bus and queue for knowledge events."""

    def __init__(self) -> None:
        self._subscribers: List[Callable[[KnowledgeEvent], None]] = []
        self._subscribers_by_type: Dict[KnowledgeEventType, List[Callable[[KnowledgeEvent], None]]] = {
            et: [] for et in KnowledgeEventType
        }
        self._queue: queue.Queue[KnowledgeEvent] = queue.Queue()
        self._history: List[KnowledgeEvent] = []
        self._lock = threading.RLock()

    def subscribe(
        self,
        callback: Callable[[KnowledgeEvent], None],
        event_type: Optional[KnowledgeEventType] = None,
    ) -> None:
        """Subscribe a callback to all events or events of a specific type."""
        with self._lock:
            if event_type is None:
                if callback not in self._subscribers:
                    self._subscribers.append(callback)
            else:
                subscribers = self._subscribers_by_type.setdefault(event_type, [])
                if callback not in subscribers:
                    subscribers.append(callback)

    def unsubscribe(
        self,
        callback: Callable[[KnowledgeEvent], None],
        event_type: Optional[KnowledgeEventType] = None,
    ) -> None:
        """Unsubscribe a callback."""
        with self._lock:
            if event_type is None:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)
                for subs in self._subscribers_by_type.values():
                    if callback in subs:
                        subs.remove(callback)
            else:
                subs = self._subscribers_by_type.get(event_type, [])
                if callback in subs:
                    subs.remove(callback)

    def publish(self, event: KnowledgeEvent, enqueue: bool = True) -> None:
        """Publish an event to synchronous subscribers and optionally queue for workers."""
        with self._lock:
            self._history.append(event)
            if enqueue:
                self._queue.put(event)

            # Copy lists to allow safe mutation during dispatch
            general_subs = list(self._subscribers)
            type_subs = list(self._subscribers_by_type.get(event.event_type, []))

        for cb in general_subs:
            try:
                cb(event)
            except Exception:
                pass

        for cb in type_subs:
            try:
                cb(event)
            except Exception:
                pass

    def enqueue(self, event: KnowledgeEvent) -> None:
        """Directly queue an event without triggering immediate callbacks."""
        with self._lock:
            self._history.append(event)
            self._queue.put(event)

    def get_next_event(self, block: bool = False, timeout: Optional[float] = None) -> Optional[KnowledgeEvent]:
        """Fetch the next queued event, returning None if empty (when non-blocking or timed out)."""
        try:
            return self._queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def task_done(self) -> None:
        """Mark task as done in the underlying queue."""
        self._queue.task_done()

    @property
    def pending_count(self) -> int:
        """Number of events currently waiting in the background queue."""
        return self._queue.qsize()

    @property
    def history(self) -> List[KnowledgeEvent]:
        """Read-only copy of published event history."""
        with self._lock:
            return list(self._history)
