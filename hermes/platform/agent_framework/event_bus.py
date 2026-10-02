"""Fast typed Event Bus with subscription routing and JSONL audit logging.

Provides synchronous in-thread dispatch, fnmatch-style wildcard routing,
in-memory history filtering, and locked, fsynced JSONL persistence. A failed append
may leave a partial line; consumers must reject malformed records.
"""

from __future__ import annotations

import collections
import fcntl
import fnmatch
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from hermes.platform.agent_framework.models import EventSeverity, HAOSEvent
from hermes.platform.agent_framework.state_manager import HAOSStateManager
from hermes_constants import mkdir_under_hermes_home

logger = logging.getLogger(__name__)


class HAOSEventBus:
    """Thread-safe event bus with topic wildcard routing and JSONL audit trail.

    Supports exact matching and wildcard patterns (e.g. 'disk.*', '*.error', '*').
    Events are synchronously dispatched to subscribers, retained in bounded memory
    history, and appended to an on-disk JSONL log file.
    """

    def __init__(
        self,
        log_dir: Path | str | None = None,
        max_history: int = 1000,
    ) -> None:
        """Initialize the event bus.

        Args:
            log_dir: Directory where agent-events.jsonl will be stored.
                     Defaults to the owning get_hermes_home() / 'agent'.
            max_history: Maximum number of recent events kept in memory.
        """
        if log_dir is not None:
            self.log_dir = Path(log_dir).resolve()
        else:
            from hermes_constants import get_hermes_home

            self.log_dir = (Path(get_hermes_home()) / "agent").resolve()

        HAOSStateManager._check_path(self.log_dir)
        mkdir_under_hermes_home(self.log_dir)
        self.events_log_file = self.log_dir / "agent-events.jsonl"

        self._max_history = max(1, max_history)
        self._history: collections.deque[HAOSEvent] = collections.deque(maxlen=self._max_history)
        self._subscribers: dict[str, tuple[str, Callable[[HAOSEvent], None]]] = {}
        self._lock = threading.RLock()

    def subscribe(self, pattern: str, handler: Callable[[HAOSEvent], None]) -> str:
        """Subscribe a callable handler to an event topic pattern.

        Args:
            pattern: Exact string or wildcard pattern (e.g. 'disk.*', 'haos.node.?').
            handler: Callable invoked synchronously with each matching HAOSEvent.

        Returns:
            Subscription ID string used to unsubscribe.
        """
        sub_id = f"sub_{uuid.uuid4().hex}"
        with self._lock:
            self._subscribers[sub_id] = (pattern, handler)
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Unsubscribe a previously registered handler.

        Args:
            sub_id: Subscription ID returned by subscribe().

        Returns:
            True if subscription was found and removed, False otherwise.
        """
        with self._lock:
            return self._subscribers.pop(sub_id, None) is not None

    def publish(self, event: HAOSEvent) -> None:
        """Dispatch event to matching handlers, record in memory, and append to JSONL.

        Args:
            event: The HAOSEvent instance to broadcast.
        """
        # Persist before admitting the event to history or dispatching side effects.
        with self._lock:
            self._log_event_atomic(event)
            self._history.append(event)
            matching_handlers: list[Callable[[HAOSEvent], None]] = [
                handler
                for pattern, handler in self._subscribers.values()
                if fnmatch.fnmatchcase(event.event_type, pattern)
            ]

        # Synchronous in-thread dispatch
        for handler in matching_handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.exception("Error executing subscriber %r for event %r: %s", handler, event.id, exc)

    def _log_event_atomic(self, event: HAOSEvent) -> None:
        """Atomically append an event as a line to agent-events.jsonl using POSIX file lock."""
        line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
        with self._lock:
            HAOSStateManager._check_path(self.events_log_file)
            fd = os.open(self.events_log_file, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    stream.write(line)
                    stream.flush()
                    os.fsync(stream.fileno())
                    directory_fd = os.open(self.log_dir, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def get_recent_events(
        self,
        limit: int = 50,
        event_type_prefix: str | None = None,
        min_severity: EventSeverity | str | None = None,
    ) -> list[HAOSEvent]:
        """Retrieve recent events from in-memory history sorted newest first.

        Args:
            limit: Maximum count of events to return.
            event_type_prefix: If specified, only events whose type starts with this prefix.
            min_severity: If specified, only events at or above this severity.

        Returns:
            List of matching HAOSEvent objects (newest first).
        """
        if limit <= 0:
            return []
        if isinstance(min_severity, str):
            min_severity = EventSeverity(min_severity.lower())

        with self._lock:
            results: list[HAOSEvent] = []
            # Traverse newest first
            for event in reversed(self._history):
                if event_type_prefix and not event.event_type.startswith(event_type_prefix):
                    continue
                if min_severity is not None and event.severity < min_severity:
                    continue
                results.append(event)
                if len(results) >= limit:
                    break
            return results

    def clear_history(self) -> None:
        """Clear all events from in-memory history buffer."""
        with self._lock:
            self._history.clear()
