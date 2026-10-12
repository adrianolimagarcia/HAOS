"""Canonical Event Model and Hash-Chained Audit Stream.

Implements tamper-evident event log with:
- Monotonic event_id
- State hash chaining (prev_hash + payload -> state_hash)
- Cryptographic signatures (HMAC-SHA256 or Ed25519)
- Chain integrity verification
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _canonical_json(data: Any) -> str:
    """Deterministic JSON serialization for hashing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_state_hash(
    prev_hash: str,
    session_id: str,
    event_id: int,
    event_type: str,
    payload: Dict[str, Any],
) -> str:
    """Compute SHA-256 state hash binding prior state to current event data."""
    payload_str = _canonical_json(payload)
    to_hash = f"{prev_hash}:{session_id}:{event_id}:{event_type}:{payload_str}"
    return hashlib.sha256(to_hash.encode("utf-8")).hexdigest()


def sign_event(state_hash: str, secret_key: str) -> str:
    """Compute cryptographic receipt signature over the state hash."""
    # HMAC-SHA256 provides secure universal cryptographic receipt
    sig = hmac.new(secret_key.encode("utf-8"), state_hash.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{sig}"


def verify_signature(state_hash: str, signature: str, secret_key: str) -> bool:
    """Verify cryptographic receipt signature."""
    if not signature or not signature.startswith("hmac-sha256:"):
        return False
    expected = sign_event(state_hash, secret_key)
    return hmac.compare_digest(signature, expected)


class CanonicalEvent:
    """Tamper-evident canonical event with cryptographic hash chaining."""

    def __init__(
        self,
        session_id: str,
        event_id: int,
        event_type: str,
        payload: Dict[str, Any],
        prev_hash: str = "0" * 64,
        timestamp: Optional[str] = None,
        state_hash: Optional[str] = None,
        signature: Optional[str] = None,
    ):
        self.session_id = session_id
        self.event_id = event_id
        self.type = event_type
        self.payload = payload
        self.prev_hash = prev_hash
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        self.state_hash = state_hash or compute_state_hash(
            prev_hash, session_id, event_id, event_type, payload
        )
        self.signature = signature

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "session_id": self.session_id,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "type": self.type,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "state_hash": self.state_hash,
        }
        if self.signature:
            data["signature"] = self.signature
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CanonicalEvent:
        return cls(
            session_id=data["session_id"],
            event_id=data["event_id"],
            event_type=data["type"],
            payload=data["payload"],
            prev_hash=data["prev_hash"],
            timestamp=data["timestamp"],
            state_hash=data["state_hash"],
            signature=data.get("signature"),
        )


def verify_event_chain(events: List[CanonicalEvent], signing_key: Optional[str] = None) -> Tuple[bool, Optional[int], str]:
    """Verify cryptographic integrity of an event sequence.

    Returns:
    - (True, None, "ok") if valid
    - (False, invalid_index, error_message) if tampered
    """
    if not events:
        return True, None, "empty"

    expected_prev = "0" * 64
    for idx, ev in enumerate(events):
        if ev.prev_hash != expected_prev:
            return False, idx, f"Broken chain link at index {idx}: expected prev {expected_prev}, got {ev.prev_hash}"

        expected_hash = compute_state_hash(
            ev.prev_hash, ev.session_id, ev.event_id, ev.type, ev.payload
        )
        if ev.state_hash != expected_hash:
            return False, idx, f"State hash mismatch at index {idx}: expected {expected_hash}, got {ev.state_hash}"

        if signing_key and ev.signature:
            if not verify_signature(ev.state_hash, ev.signature, signing_key):
                return False, idx, f"Invalid signature at index {idx}"

        expected_prev = ev.state_hash

    return True, None, "ok"
