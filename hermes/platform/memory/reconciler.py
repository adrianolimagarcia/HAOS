"""HAOS Memory Reconciler (Mem0-inspired Semantic Mutation & Conflict Resolution).

Provides deterministic lifecycle and reconciliation for declarative memories:
- Classifies incoming facts into:
    ADD: Novel knowledge or entity relationship.
    UPDATE: Enrichment or refinement of existing knowledge.
    SUPERSEDE: Invalidation or replacement of contradictory past facts.
    NOOP: Duplicate or redundant facts already captured.
- Preserves audit trail: superseded memories receive `status='superseded'` and
  `superseded_by=<new_id>` in SQLite.
- Strict prompt hygiene: prompt injection filters `WHERE status = 'active'`,
  guaranteeing that stale or contradictory facts never contaminate context.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("haos.memory.reconciler")

_TRANSITION_MARKERS = (
    "não usamos mais", "não usa mais", "migrei de", "migramos de",
    "substituído por", "substituido por", "trocado por", "agora usamos",
    "mudou para", "passou a ser", "ao invés de", "ao inves de",
    "preferir agora", "descontinuado", "depreciado", "não é mais",
    "replaced with", "switched to", "migrated from", "no longer using",
    "now using", "deprecated", "instead of", "changed to", "superseded by",
)


@dataclass
class MemoryRecord:
    id: str
    category: str
    topic: str
    content: str
    confidence: float
    status: str  # 'active' | 'superseded' | 'deleted'
    superseded_by: Optional[str]
    created_at: float
    updated_at: float
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReconciliationResult:
    action: str  # 'ADD' | 'UPDATE' | 'SUPERSEDE' | 'NOOP'
    memory_id: str
    target_memory_id: Optional[str] = None
    reason: str = ""
    old_content: Optional[str] = None
    new_content: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MemoryReconciler:
    """SQLite-backed Memory Reconciler managing lifecycle, mutations and conflict resolution."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            from hermes_constants import get_hermes_home
            home = Path(get_hermes_home())
            db_path = home / "memory" / "reconciled_memories.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS haos_memories (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    content TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    status TEXT DEFAULT 'active',
                    superseded_by TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    metadata TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_haos_mem_topic ON haos_memories(topic, status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_haos_mem_status ON haos_memories(status);")
            conn.commit()

    @staticmethod
    def _normalize_tokens(text: str) -> set[str]:
        words = re.findall(r"\b\w+\b", text.lower())
        return set(words)

    def _detect_action(
        self,
        new_content: str,
        existing: MemoryRecord,
    ) -> Tuple[str, str]:
        """Detect action between new content and existing active record for the same topic.

        Returns (action, reason).
        """
        old_clean = existing.content.strip().lower()
        new_clean = new_content.strip().lower()

        if old_clean == new_clean:
            return "NOOP", "Exact duplicate content"

        old_tokens = self._normalize_tokens(old_clean)
        new_tokens = self._normalize_tokens(new_clean)

        if not new_tokens:
            return "NOOP", "Empty incoming content"

        # Check for explicit transition/superseding language
        has_transition = any(m in new_clean for m in _TRANSITION_MARKERS)
        if has_transition:
            return "SUPERSEDE", "Contains explicit transition/migration/negation markers"

        # Calculate Jaccard similarity
        intersection = old_tokens & new_tokens
        union = old_tokens | new_tokens
        jaccard = len(intersection) / len(union) if union else 0.0

        if jaccard > 0.85:
            return "NOOP", f"High lexical equivalence (similarity={jaccard:.2f})"

        # If similarity is moderate and content adds more details
        if 0.35 <= jaccard <= 0.85 and len(new_clean) > len(old_clean):
            return "UPDATE", f"Refines and enriches existing knowledge (similarity={jaccard:.2f})"

        # If content for the same topic asserts a conflicting fact
        # (low lexical overlap or distinct value for same topic)
        return "SUPERSEDE", f"Contradicts or replaces past statement on topic '{existing.topic}'"

    def reconcile(
        self,
        *,
        topic: str,
        content: str,
        category: str = "general",
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReconciliationResult:
        """Evaluate candidate memory against existing active memories on this topic."""
        clean_content = content.strip()
        clean_topic = topic.strip().lower()
        if not clean_content:
            return ReconciliationResult(
                action="NOOP",
                memory_id="",
                reason="Empty content provided",
            )

        meta = metadata or {}
        now = time.time()

        with self._lock, self._get_connection() as conn:
            # Query existing active memories on the same topic
            cur = conn.execute(
                "SELECT * FROM haos_memories WHERE topic = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1;",
                (clean_topic,),
            )
            row = cur.fetchone()

            if not row:
                # Novel fact -> ADD
                new_id = f"mem_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO haos_memories (
                        id, category, topic, content, confidence, status, superseded_by, created_at, updated_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, 'active', NULL, ?, ?, ?);
                    """,
                    (new_id, category, clean_topic, clean_content, confidence, now, now, json.dumps(meta, ensure_ascii=False)),
                )
                conn.commit()
                return ReconciliationResult(
                    action="ADD",
                    memory_id=new_id,
                    reason="Novel fact registered for topic",
                    new_content=clean_content,
                )

            existing = MemoryRecord(
                id=row["id"],
                category=row["category"],
                topic=row["topic"],
                content=row["content"],
                confidence=float(row["confidence"]),
                status=row["status"],
                superseded_by=row["superseded_by"],
                created_at=float(row["created_at"]),
                updated_at=float(row["updated_at"]),
                metadata=json.loads(row["metadata"] or "{}"),
            )

            action, reason = self._detect_action(clean_content, existing)

            if action == "NOOP":
                return ReconciliationResult(
                    action="NOOP",
                    memory_id=existing.id,
                    target_memory_id=existing.id,
                    reason=reason,
                    old_content=existing.content,
                    new_content=clean_content,
                )

            if action == "UPDATE":
                conn.execute(
                    "UPDATE haos_memories SET content = ?, confidence = ?, updated_at = ?, metadata = ? WHERE id = ?;",
                    (clean_content, confidence, now, json.dumps({**existing.metadata, **meta}, ensure_ascii=False), existing.id),
                )
                conn.commit()
                return ReconciliationResult(
                    action="UPDATE",
                    memory_id=existing.id,
                    target_memory_id=existing.id,
                    reason=reason,
                    old_content=existing.content,
                    new_content=clean_content,
                )

            # SUPERSEDE: Mark old memory as superseded and insert new memory as active
            new_id = f"mem_{uuid.uuid4().hex[:12]}"
            conn.execute(
                "UPDATE haos_memories SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?;",
                (new_id, now, existing.id),
            )
            conn.execute(
                """
                INSERT INTO haos_memories (
                    id, category, topic, content, confidence, status, superseded_by, created_at, updated_at, metadata
                ) VALUES (?, ?, ?, ?, ?, 'active', NULL, ?, ?, ?);
                """,
                (new_id, category, clean_topic, clean_content, confidence, now, now, json.dumps(meta, ensure_ascii=False)),
            )
            conn.commit()
            return ReconciliationResult(
                action="SUPERSEDE",
                memory_id=new_id,
                target_memory_id=existing.id,
                reason=reason,
                old_content=existing.content,
                new_content=clean_content,
            )

    def get_active_memories(
        self,
        *,
        topic: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> List[MemoryRecord]:
        """Fetch only active memories for prompt injection (guarantees superseded facts are excluded)."""
        query = "SELECT * FROM haos_memories WHERE status = 'active'"
        params: List[Any] = []

        if topic:
            query += " AND topic = ?"
            params.append(topic.strip().lower())
        if category:
            query += " AND category = ?"
            params.append(category.strip())

        query += " ORDER BY updated_at DESC LIMIT ?;"
        params.append(limit)

        with self._lock, self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                MemoryRecord(
                    id=r["id"],
                    category=r["category"],
                    topic=r["topic"],
                    content=r["content"],
                    confidence=float(r["confidence"]),
                    status=r["status"],
                    superseded_by=r["superseded_by"],
                    created_at=float(r["created_at"]),
                    updated_at=float(r["updated_at"]),
                    metadata=json.loads(r["metadata"] or "{}"),
                )
                for r in rows
            ]

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        """Get memory record by ID (whether active or superseded)."""
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT * FROM haos_memories WHERE id = ?;", (memory_id,)).fetchone()
            if not row:
                return None
            return MemoryRecord(
                id=row["id"],
                category=row["category"],
                topic=row["topic"],
                content=row["content"],
                confidence=float(row["confidence"]),
                status=row["status"],
                superseded_by=row["superseded_by"],
                created_at=float(row["created_at"]),
                updated_at=float(row["updated_at"]),
                metadata=json.loads(row["metadata"] or "{}"),
            )

    def format_prompt_context(self, category: Optional[str] = None) -> str:
        """Format active memories into clean prompt context block."""
        active = self.get_active_memories(category=category)
        if not active:
            return ""
        lines = ["[RECONCILED ACTIVE MEMORIES]"]
        for mem in active:
            lines.append(f"• [{mem.topic}] {mem.content}")
        return "\n".join(lines)
