"""Canonical transactional source of truth for HAOS Memory Fabric."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import time
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

VALID_SCOPES = frozenset(("private", "team", "project", "global"))

@dataclass(frozen=True)
class MemoryRecord:
    record_id: str
    logical_id: str
    revision: int
    scope: str
    content: str
    kind: str = "fact"
    status: str = "active"
    confidence: float = 1.0
    provenance: Tuple[Dict[str, Any], ...] = ()
    valid_from: float = 0.0
    valid_until: Optional[float] = None
    supersedes: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

class CanonicalMemoryStore:
    """SQLite journal and transactional outbox; projections are never writers."""
    PROJECTIONS = ("obsidian", "decisions", "graphrag")

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _migrate(self) -> None:
        with self._tx() as db:
            db.execute("CREATE TABLE IF NOT EXISTS memory_schema_version (version INTEGER NOT NULL)")
            if db.execute("SELECT COUNT(*) FROM memory_schema_version").fetchone()[0] == 0:
                db.execute("INSERT INTO memory_schema_version VALUES (1)")
            db.execute("CREATE TABLE IF NOT EXISTS memory_records (record_id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, revision INTEGER NOT NULL, scope TEXT NOT NULL CHECK(scope IN ('private','team','project','global')), kind TEXT NOT NULL, status TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL, confidence REAL NOT NULL, provenance_json TEXT NOT NULL, metadata_json TEXT NOT NULL, valid_from REAL NOT NULL, valid_until REAL, supersedes_json TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(logical_id, revision))")
            db.execute("CREATE INDEX IF NOT EXISTS idx_memory_active_scope ON memory_records(scope, logical_id, revision DESC)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_dedupe ON memory_records(scope, kind, content_hash, status)")
            db.execute("CREATE TABLE IF NOT EXISTS memory_outbox (event_id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES memory_records(record_id), event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at REAL NOT NULL, available_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, lease_until REAL, last_error TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS memory_projection_ack (event_id TEXT NOT NULL REFERENCES memory_outbox(event_id), projection TEXT NOT NULL, applied_at REAL NOT NULL, PRIMARY KEY(event_id, projection))")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(record_id UNINDEXED, content, tokenize='unicode61 remove_diacritics 2')")
            db.execute("CREATE TRIGGER IF NOT EXISTS memory_records_ai AFTER INSERT ON memory_records BEGIN INSERT INTO memory_fts(record_id, content) VALUES (new.record_id, new.content); END")

    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.sha256(" ".join(content.split()).casefold().encode("utf-8")).hexdigest()

    def append(self, *, content: str, scope: str, kind: str = "fact", logical_id: Optional[str] = None, provenance: Sequence[Dict[str, Any]] = (), confidence: float = 1.0, metadata: Optional[Dict[str, Any]] = None, supersedes: Sequence[str] = (), idempotency_key: Optional[str] = None, valid_from: Optional[float] = None) -> MemoryRecord:
        if scope not in VALID_SCOPES:
            raise ValueError("invalid scope: %r" % scope)
        if not content or not content.strip():
            raise ValueError("content must not be empty")
        now = time.time() if valid_from is None else valid_from
        content_hash = self._hash(content)
        logical_id = logical_id or str(uuid.uuid4())
        record_id = idempotency_key or str(uuid.uuid4())
        metadata = dict(metadata or {})
        with self._tx() as db:
            existing = db.execute("SELECT * FROM memory_records WHERE record_id=?", (record_id,)).fetchone()
            if existing is not None:
                return self._row(existing)
            duplicate = db.execute("SELECT * FROM memory_records WHERE scope=? AND kind=? AND content_hash=? AND status='active'", (scope, kind, content_hash)).fetchone()
            if duplicate is not None:
                return self._row(duplicate)
            revision = db.execute("SELECT COALESCE(MAX(revision), 0) FROM memory_records WHERE logical_id=?", (logical_id,)).fetchone()[0] + 1
            record = MemoryRecord(record_id, logical_id, revision, scope, content, kind, "active", max(0.0, min(1.0, float(confidence))), tuple(dict(x) for x in provenance), now, None, tuple(supersedes), metadata, content_hash)
            db.execute("INSERT INTO memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (record.record_id, record.logical_id, record.revision, record.scope, record.kind, record.status, record.content, record.content_hash, record.confidence, json.dumps(record.provenance, sort_keys=True), json.dumps(record.metadata, sort_keys=True), record.valid_from, None, json.dumps(record.supersedes), now))
            if supersedes:
                db.executemany("UPDATE memory_records SET status='superseded', valid_until=? WHERE record_id=? AND status='active'", [(now, item) for item in supersedes])
            db.execute("INSERT INTO memory_outbox VALUES (?,?,?,?,?,?,?,?,?,?)", ("memory.changed:" + record.record_id, record.record_id, "memory.changed", json.dumps(asdict(record), sort_keys=True, default=list), now, now, 0, None, None, None))
            return record

    def claim(self, projection: str, worker_id: str, limit: int = 32, lease_seconds: float = 60.0) -> List[Tuple[str, MemoryRecord]]:
        if projection not in self.PROJECTIONS:
            raise ValueError("unknown projection: %r" % projection)
        now = time.time()
        with self._tx() as db:
            rows = db.execute("SELECT o.event_id, r.* FROM memory_outbox o JOIN memory_records r ON r.record_id=o.record_id WHERE o.available_at<=? AND (o.lease_until IS NULL OR o.lease_until<?) AND NOT EXISTS (SELECT 1 FROM memory_projection_ack a WHERE a.event_id=o.event_id AND a.projection=?) ORDER BY o.created_at LIMIT ?", (now, now, projection, limit)).fetchall()
            if rows:
                db.executemany("UPDATE memory_outbox SET lease_owner=?, lease_until=?, attempts=attempts+1 WHERE event_id=?", [(worker_id, now + lease_seconds, r["event_id"]) for r in rows])
            return [(r["event_id"], self._row(r)) for r in rows]

    def ack(self, event_id: str, projection: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR IGNORE INTO memory_projection_ack VALUES (?,?,?)", (event_id, projection, time.time()))
            db.execute("UPDATE memory_outbox SET lease_owner=NULL, lease_until=NULL, last_error=NULL WHERE event_id=?", (event_id,))

    def fail(self, event_id: str, error: str, retry_after: float = 1.0) -> None:
        with self._tx() as db:
            db.execute("UPDATE memory_outbox SET lease_owner=NULL, lease_until=NULL, available_at=?, last_error=? WHERE event_id=?", (time.time() + max(0.0, retry_after), error[:1000], event_id))

    def search_fts(self, query: str, scopes: Sequence[str], limit: int = 20) -> List[MemoryRecord]:
        if not query.strip() or not scopes:
            return []
        marks = ",".join("?" for _ in scopes)
        with self._lock:
            rows = self._conn.execute("SELECT r.* FROM memory_fts f JOIN memory_records r ON r.record_id=f.record_id WHERE memory_fts MATCH ? AND r.status='active' AND r.scope IN (" + marks + ") ORDER BY bm25(memory_fts) LIMIT ?", (query, *scopes, limit)).fetchall()
        return [self._row(row) for row in rows]

    def active_by_ids(self, record_ids: Sequence[str], scopes: Sequence[str]) -> List[MemoryRecord]:
        """Resolve index candidates through canonical scope/status policy."""
        if not record_ids or not scopes:
            return []
        ids = ",".join("?" for _ in record_ids)
        marks = ",".join("?" for _ in scopes)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_records WHERE status='active' AND record_id IN (" + ids + ") AND scope IN (" + marks + ")",
                (*record_ids, *scopes),
            ).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(row["record_id"], row["logical_id"], row["revision"], row["scope"], row["content"], row["kind"], row["status"], row["confidence"], tuple(json.loads(row["provenance_json"])), row["valid_from"], row["valid_until"], tuple(json.loads(row["supersedes_json"])), json.loads(row["metadata_json"]), row["content_hash"])
