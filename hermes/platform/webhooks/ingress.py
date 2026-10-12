"""Webhook Ingress Handler for capability URLs.

Enforces:
1. Immediate 401 if secret does not match BEFORE processing body.
2. Filtering by event_types.
3. Deduplication by delivery-id (24h TTL cache).
4. Sliding window rate limiting.
5. Delivery modes: 'run' (enqueues turn) vs 'post' (records message only).
"""

from __future__ import annotations

import collections
import time
from typing import Any, Callable, Dict, Optional, Set, Tuple

from hermes.platform.webhooks.models import Webhook
from hermes.platform.webhooks.store import WebhookStore, get_webhook_store


class DeliveryDeduplicator:
    """Stores delivery IDs with a 24-hour expiration window."""
    def __init__(self, ttl_seconds: float = 86400.0):
        self.ttl_seconds = ttl_seconds
        # maps delivery_id -> timestamp
        self._seen: Dict[str, float] = {}

    def is_duplicate(self, delivery_id: str) -> bool:
        if not delivery_id:
            return False
        now = time.time()
        self._evict_expired(now)
        if delivery_id in self._seen:
            return True
        self._seen[delivery_id] = now
        return False

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        expired = [did for did, ts in self._seen.items() if ts < cutoff]
        for did in expired:
            del self._seen[did]


class RateLimiter:
    """Per-webhook sliding-window rate limiter."""
    def __init__(self, window_seconds: float = 60.0):
        self.window_seconds = window_seconds
        self._timestamps: Dict[str, collections.deque] = collections.defaultdict(collections.deque)

    def allow(self, webhook_id: str, limit: int) -> bool:
        now = time.time()
        q = self._timestamps[webhook_id]
        cutoff = now - self.window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


class IngressResult:
    def __init__(
        self,
        status_code: int,
        message: str,
        delivered: bool = False,
        delivery_mode: Optional[str] = None,
        ignored: bool = False,
    ):
        self.status_code = status_code
        self.message = message
        self.delivered = delivered
        self.delivery_mode = delivery_mode
        self.ignored = ignored

    def to_dict(self) -> dict:
        return {
            "status_code": self.status_code,
            "message": self.message,
            "delivered": self.delivered,
            "delivery_mode": self.delivery_mode,
            "ignored": self.ignored,
        }


class WebhookIngressService:
    def __init__(
        self,
        store: Optional[WebhookStore] = None,
        turn_runner_cb: Optional[Callable[[dict], Any]] = None,
        message_poster_cb: Optional[Callable[[dict], Any]] = None,
    ):
        self.store = store or get_webhook_store()
        self.deduplicator = DeliveryDeduplicator()
        self.rate_limiter = RateLimiter()
        self.turn_runner_cb = turn_runner_cb
        self.message_poster_cb = message_poster_cb

    def authenticate_request(self, webhook_id: str, candidate_secret: str) -> Tuple[bool, Optional[Webhook]]:
        """Step 1: Check capability credentials BEFORE inspecting body."""
        wh = self.store.get(webhook_id)
        if not wh or not wh.active:
            return False, None
        if not wh.verify_secret(candidate_secret):
            return False, None
        return True, wh

    def process_payload(
        self,
        webhook: Webhook,
        payload: dict,
        headers: Optional[dict] = None,
    ) -> IngressResult:
        """Step 2: Process validated webhook payload."""
        headers = headers or {}
        # Rate limit check
        if not self.rate_limiter.allow(webhook.id, webhook.rate_limit):
            return IngressResult(429, "Rate limit exceeded", delivered=False)

        # Delivery-ID deduplication
        delivery_id = (
            headers.get("x-delivery-id")
            or headers.get("delivery-id")
            or payload.get("delivery_id")
            or payload.get("id")
        )
        if delivery_id and self.deduplicator.is_duplicate(str(delivery_id)):
            return IngressResult(200, "Duplicate delivery-id ignored", delivered=False, ignored=True)

        # Event-type filtering
        event_type = (
            headers.get("x-event-type")
            or headers.get("x-github-event")
            or payload.get("event_type")
            or payload.get("type")
            or "default"
        )
        if not webhook.accepts_event(str(event_type)):
            return IngressResult(200, f"Event {event_type} not subscribed", delivered=False, ignored=True)

        # Delivery execution
        if webhook.delivery == "run":
            if self.turn_runner_cb:
                self.turn_runner_cb({"webhook_id": webhook.id, "event": event_type, "payload": payload})
            return IngressResult(200, "Enqueued agent turn", delivered=True, delivery_mode="run")
        elif webhook.delivery == "post":
            if self.message_poster_cb:
                self.message_poster_cb({"webhook_id": webhook.id, "event": event_type, "payload": payload})
            return IngressResult(200, "Posted message to thread", delivered=True, delivery_mode="post")

        return IngressResult(200, "Processed", delivered=True, delivery_mode=webhook.delivery)
