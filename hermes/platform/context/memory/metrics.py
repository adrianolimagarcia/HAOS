"""Read-only operational metrics for Memory Fabric."""
from __future__ import annotations
from typing import Dict
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore

def snapshot(store: CanonicalMemoryStore) -> Dict[str, int]:
    with store._lock:  # Store-owned lock; metrics must not race transactions.
        db = store._conn
        return {
            "records_active": db.execute("SELECT COUNT(*) FROM memory_records WHERE status='active'").fetchone()[0],
            "records_superseded": db.execute("SELECT COUNT(*) FROM memory_records WHERE status='superseded'").fetchone()[0],
            "outbox_events": db.execute("SELECT COUNT(*) FROM memory_outbox").fetchone()[0],
            "projection_backlog": db.execute("SELECT COUNT(*) FROM memory_projection_jobs").fetchone()[0],
            "projection_retries": db.execute("SELECT COALESCE(SUM(attempts), 0) FROM memory_projection_jobs").fetchone()[0],
            "projection_failures": db.execute("SELECT COUNT(*) FROM memory_projection_jobs WHERE last_error IS NOT NULL").fetchone()[0],
        }
