"""Durable outbox worker for rebuildable Memory Fabric projections."""
from __future__ import annotations
import logging
from typing import Callable, Dict
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore, MemoryRecord

logger = logging.getLogger(__name__)
Projector = Callable[[MemoryRecord], None]

class ProjectionRunner:
    """Claims and acknowledges each projection independently.

    Projectors receive only canonical records. They must perform deterministic
    upserts keyed by record_id; failures remain durable jobs for retry.
    """

    def __init__(self, store: CanonicalMemoryStore, projectors: Dict[str, Projector], worker_id: str = "memory-projection", lease_seconds: float = 60.0) -> None:
        missing = set(store.PROJECTIONS) - set(projectors)
        if missing:
            raise ValueError("missing projectors: %s" % sorted(missing))
        self.store = store
        self.projectors = dict(projectors)
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def drain(self, limit_per_projection: int = 32) -> int:
        applied = 0
        for projection in self.store.PROJECTIONS:
            for event_id, record in self.store.claim(projection, self.worker_id, limit_per_projection, self.lease_seconds):
                try:
                    self.projectors[projection](record)
                except Exception as exc:
                    self.store.fail(event_id, projection, repr(exc))
                    logger.exception("Memory projection %s failed for %s", projection, event_id)
                else:
                    self.store.ack(event_id, projection)
                    applied += 1
        return applied
