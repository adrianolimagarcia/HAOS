"""Command inbox for idempotency and duplicate deduplication in Council operations."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event

_COMMAND_RECEIVED = "civ.command.received"
_COMMAND_COMPLETED = "civ.command.completed"


def compute_payload_hash(payload: Any) -> str:
    """Deterministic hash of payload for integrity and deduplication check."""
    normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class CommandInbox:
    """Manages command idempotency using the event-sourced EventStore."""

    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    def is_processed(self, command_id: str) -> bool:
        """Check if command_id has already been processed to completion."""
        return self.get_result(command_id) is not None

    def get_result(self, command_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve the persisted outcome of a previously completed command."""
        for e in self.event_store.get_all(name=_COMMAND_COMPLETED):
            if e.payload.get("command_id") == command_id:
                return e.payload.get("result")
        return None

    def record_received(
        self,
        command_id: str,
        command_type: str,
        payload: Dict[str, Any],
        actor: str = "operator",
    ) -> bool:
        """Record receipt of command. Returns False if already seen (duplicate)."""
        for e in self.event_store.get_all(name=_COMMAND_RECEIVED):
            if e.payload.get("command_id") == command_id:
                return False  # Already registered

        payload_hash = compute_payload_hash(payload)
        self.event_store.append(
            Event(
                name=_COMMAND_RECEIVED,
                payload={
                    "command_id": command_id,
                    "command_type": command_type,
                    "payload_hash": payload_hash,
                    "actor": actor,
                    "received_at": time.time(),
                },
                correlation_id=command_id,
            )
        )
        return True

    def record_completed(
        self,
        command_id: str,
        result: Dict[str, Any],
        status: str = "succeeded",
        error: Optional[str] = None,
    ) -> None:
        """Record successful or failed terminal state of a command."""
        self.event_store.append(
            Event(
                name=_COMMAND_COMPLETED,
                payload={
                    "command_id": command_id,
                    "status": status,
                    "result": result,
                    "error": error,
                    "completed_at": time.time(),
                },
                correlation_id=command_id,
            )
        )


__all__ = ["CommandInbox", "compute_payload_hash"]
