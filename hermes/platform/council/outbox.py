"""Transactional outbox for Council action intents and asynchronous side effects."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event

_ACTION_INTENT_CREATED = "civ.council.action-intent-created"
_ACTION_COMPLETED = "civ.council.action-completed"
_ACTION_FAILED = "civ.council.action-failed"


@dataclass
class ActionIntent:
    intent_id: str
    council_id: str
    session_id: str
    action_type: str
    payload: Dict[str, Any]
    status: str = "pending"  # pending, executing, completed, failed, dead_letter
    attempts: int = 0
    max_attempts: int = 3
    lease_owner: Optional[str] = None
    lease_expires_at: float = 0.0
    result: Optional[Dict[str, Any]] = None
    last_error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActionIntent":
        valid = {
            "intent_id", "council_id", "session_id", "action_type",
            "payload", "status", "attempts", "max_attempts",
            "lease_owner", "lease_expires_at", "result", "last_error",
            "created_at", "updated_at"
        }
        return cls(**{k: data[k] for k in valid if k in data})


class CouncilOutbox:
    """Outbox manager providing durable intent tracking, leases, and safe execution."""

    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    def _get_all_intents(self) -> Dict[str, ActionIntent]:
        intents: Dict[str, ActionIntent] = {}
        for e in self.event_store.get_all():
            if e.name == _ACTION_INTENT_CREATED:
                d = e.payload.get("intent")
                if d:
                    intent = ActionIntent.from_dict(d)
                    intents[intent.intent_id] = intent
            elif e.name == _ACTION_COMPLETED:
                intent_id = e.payload.get("intent_id")
                if intent_id in intents:
                    intents[intent_id].status = "completed"
                    intents[intent_id].result = e.payload.get("result")
                    intents[intent_id].updated_at = e.payload.get("timestamp", time.time())
            elif e.name == _ACTION_FAILED:
                intent_id = e.payload.get("intent_id")
                if intent_id in intents:
                    intents[intent_id].attempts = e.payload.get("attempts", intents[intent_id].attempts + 1)
                    intents[intent_id].last_error = e.payload.get("error")
                    if intents[intent_id].attempts >= intents[intent_id].max_attempts:
                        intents[intent_id].status = "dead_letter"
                    else:
                        intents[intent_id].status = "pending"
                    intents[intent_id].updated_at = e.payload.get("timestamp", time.time())
        return intents

    def schedule_intent(
        self,
        council_id: str,
        session_id: str,
        action_type: str,
        payload: Dict[str, Any],
        max_attempts: int = 3,
        correlation_id: Optional[str] = None,
    ) -> ActionIntent:
        """Create and persist a new action intent before executing side effects."""
        intent_id = f"act-{session_id}-{uuid.uuid4().hex[:8]}"
        intent = ActionIntent(
            intent_id=intent_id,
            council_id=council_id,
            session_id=session_id,
            action_type=action_type,
            payload=payload,
            max_attempts=max_attempts,
        )
        self.event_store.append(
            Event(
                name=_ACTION_INTENT_CREATED,
                payload={"intent": intent.to_dict(), "timestamp": time.time()},
                correlation_id=correlation_id or session_id,
            )
        )
        return intent

    def get_pending(self) -> List[ActionIntent]:
        """Return all pending intents whose leases have expired or never claimed."""
        now = time.time()
        intents = self._get_all_intents()
        return [
            intent for intent in intents.values()
            if intent.status == "pending" and intent.lease_expires_at <= now
        ]

    def claim_lease(self, intent_id: str, worker_id: str, lease_seconds: float = 30.0) -> bool:
        """Attempt to claim execution lease for an intent."""
        now = time.time()
        intents = self._get_all_intents()
        intent = intents.get(intent_id)
        if not intent:
            return False
        if intent.status != "pending" or (intent.lease_expires_at > now and intent.lease_owner != worker_id):
            return False
        intent.lease_owner = worker_id
        intent.lease_expires_at = now + lease_seconds
        intent.status = "executing"
        return True

    def mark_completed(
        self,
        intent_id: str,
        result: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Record successful execution of the intent."""
        self.event_store.append(
            Event(
                name=_ACTION_COMPLETED,
                payload={"intent_id": intent_id, "result": result or {}, "timestamp": time.time()},
                correlation_id=correlation_id,
            )
        )

    def mark_failed(
        self,
        intent_id: str,
        error: str,
        attempts: int,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Record failure and increment attempt count."""
        self.event_store.append(
            Event(
                name=_ACTION_FAILED,
                payload={"intent_id": intent_id, "error": error, "attempts": attempts, "timestamp": time.time()},
                correlation_id=correlation_id,
            )
        )

    def dispatch_pending(
        self,
        worker_id: str,
        handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Process eligible pending intents using registered action handlers."""
        results = []
        for intent in self.get_pending():
            if not self.claim_lease(intent.intent_id, worker_id):
                continue
            handler = handlers.get(intent.action_type)
            if not handler:
                self.mark_failed(intent.intent_id, f"No handler registered for {intent.action_type}", intent.attempts + 1)
                continue
            try:
                res = handler(intent.payload)
                self.mark_completed(intent.intent_id, res, correlation_id=intent.session_id)
                results.append({"intent_id": intent.intent_id, "status": "completed", "result": res})
            except Exception as exc:
                self.mark_failed(intent.intent_id, str(exc), intent.attempts + 1, correlation_id=intent.session_id)
                results.append({"intent_id": intent.intent_id, "status": "failed", "error": str(exc)})
        return results


__all__ = ["ActionIntent", "CouncilOutbox"]
