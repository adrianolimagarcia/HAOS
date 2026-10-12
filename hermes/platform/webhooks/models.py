"""Webhook Models for HAOS Ingress Triggers.

Capability URLs: /hooks/wh_<id>/<secret>
Secrets are stored strictly as SHA-256 hashes, never in plain text.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Tuple


def hash_secret(raw_secret: str) -> str:
    """Compute SHA-256 hash of a raw secret."""
    return hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()


def verify_secret_hash(raw_secret: str, stored_hash: str) -> bool:
    """Constant-time verification of a raw secret against its SHA-256 hash."""
    computed = hash_secret(raw_secret)
    return hmac.compare_digest(computed, stored_hash)


DeliveryMode = Literal["run", "post"]


@dataclass
class Webhook:
    id: str  # wh_<random>
    secret_hash: str  # sha256 hex string
    event_types: List[str] = field(default_factory=list)
    delivery: DeliveryMode = "run"
    active: bool = True
    rate_limit: int = 10  # requests per minute
    created_at: float = field(default_factory=time.time)
    description: str = ""

    def verify_secret(self, candidate_secret: str) -> bool:
        if not self.active:
            return False
        return verify_secret_hash(candidate_secret, self.secret_hash)

    def accepts_event(self, event_type: str) -> bool:
        if not self.event_types:
            return True
        return event_type in self.event_types or "*" in self.event_types

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "secret_hash": self.secret_hash,
            "event_types": self.event_types,
            "delivery": self.delivery,
            "active": self.active,
            "rate_limit": self.rate_limit,
            "created_at": self.created_at,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Webhook:
        return cls(
            id=data["id"],
            secret_hash=data["secret_hash"],
            event_types=list(data.get("event_types", [])),
            delivery=data.get("delivery", "run"),
            active=bool(data.get("active", True)),
            rate_limit=int(data.get("rate_limit", 10)),
            created_at=float(data.get("created_at", time.time())),
            description=str(data.get("description", "")),
        )


def generate_webhook(
    event_types: Optional[List[str]] = None,
    delivery: DeliveryMode = "run",
    rate_limit: int = 10,
    description: str = "",
) -> Tuple[Webhook, str]:
    """Generate a new Webhook and return (Webhook, raw_secret).

    The raw secret is only returned once upon generation and must be given
    to the user; only secret_hash is persisted.
    """
    raw_secret = secrets.token_urlsafe(32)
    wh_id = f"wh_{secrets.token_hex(8)}"
    secret_hash = hash_secret(raw_secret)
    webhook = Webhook(
        id=wh_id,
        secret_hash=secret_hash,
        event_types=event_types or [],
        delivery=delivery,
        active=True,
        rate_limit=rate_limit,
        created_at=time.time(),
        description=description,
    )
    return webhook, raw_secret
