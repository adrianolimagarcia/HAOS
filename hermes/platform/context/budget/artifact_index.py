"""Persistent, on-demand context artifacts for HAOS.

This is deliberately independent of the canonical memory/dream store: tool outputs
are ephemeral evidence, not promoted long-term user memories. No external deps.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home

_URI = re.compile(r"^artifact://store/[0-9a-f]{32}$")
_WORD = re.compile(r"\w+", re.UNICODE)
_DEFAULT_TTL = 30 * 86400
# Checkpoint retention (B-d): checkpoints were INSERT-only with no expiry. Keep
# the newest N per (scope, session) and drop anything older than the TTL.
_CHECKPOINT_KEEP_PER_SESSION = 20
_CHECKPOINT_TTL_SECONDS = 30 * 86400
_MAX_CONTENT_CHARS = 2_000_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_artifacts (
    uri TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT NOT NULL,
    abstract TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_context_artifacts_expiry
    ON context_artifacts(expires_at);
CREATE TABLE IF NOT EXISTS context_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    raw_chars INTEGER NOT NULL,
    returned_chars INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_context_usage_scope
    ON context_usage(scope, id);
CREATE TABLE IF NOT EXISTS context_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_context_checkpoints_session
    ON context_checkpoints(scope, session_id, id);
"""


def _default_path() -> Path:
    return Path(get_hermes_home()) / "context-artifacts" / "artifacts.sqlite3"


class ContextArtifactIndex:
    """Profile-local, SQLite-backed artifact recovery, exact text search, and checkpoints.

    Access is confined to the current HAOS profile and an opaque URI. The caller
    supplies a trusted scope (never a scope chosen from untrusted tool arguments).
    """

    def __init__(self, db_path: Optional[Path] = None, *, scope: str = "default") -> None:
        self.path = Path(db_path) if db_path is not None else _default_path()
        self.scope = scope
        if not scope or len(scope) > 256:
            raise ValueError("invalid artifact scope")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if db_path is None and os.name == "posix":
            os.chmod(self.path.parent, 0o700)
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                conn.executescript(_SCHEMA)
                try:
                    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS context_artifact_fts "
                                 "USING fts5(uri UNINDEXED, title, content)")
                    self.fts_enabled = True
                except sqlite3.OperationalError:
                    self.fts_enabled = False
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def put(self, content: str, *, title: str = "", kind: str = "tool",
            source: str = "", summary: str = "", abstract: str = "",
            ttl_seconds: int = _DEFAULT_TTL) -> str:
        if not isinstance(content, str) or not content:
            raise ValueError("artifact content must be non-empty text")
        if len(content) > _MAX_CONTENT_CHARS:
            raise ValueError("artifact exceeds lossless storage limit")
        uri = "artifact://store/" + uuid.uuid4().hex
        now = time.time()
        summary = summary or content[:600]
        abstract = abstract or content[:160]
        with closing(self._connect()) as conn:
            with conn:
                cur = conn.execute(
                    "INSERT INTO context_artifacts "
                    "(uri,scope,title,kind,source,content,summary,abstract,created_at,expires_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (uri, self.scope, title[:500], kind[:100], source[:500],
                     content, summary[:2000], abstract[:500], now,
                     now + max(60, int(ttl_seconds))),
                )
                if self.fts_enabled:
                    conn.execute(
                        "INSERT INTO context_artifact_fts(rowid,uri,title,content) VALUES (?,?,?,?)",
                        (cur.lastrowid, uri, title[:500], content),
                    )
        return uri

    def read(self, uri: str, *, level: str = "full", section: Optional[str] = None,
             offset: int = 0, max_chars: int = 6000) -> Optional[dict[str, Any]]:
        if not _URI.fullmatch(uri):
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT title, kind, source, content, summary, abstract, created_at "
                "FROM context_artifacts WHERE uri=? AND scope=? AND expires_at>?",
                (uri, self.scope, time.time()),
            ).fetchone()
        if row is None:
            return None
        if level not in ("full", "summary", "abstract"):
            raise ValueError("invalid level")
        if level != "full":
            return dict(status="expanded", uri=uri, title=row["title"],
                        kind=row["kind"], source=row["source"], level=level,
                        content=row[level], has_more=False)
        content = row["content"]
        start = max(0, int(offset))
        if section and not start:
            match = content.casefold().find(str(section)[:250].casefold())
            if match < 0:
                return dict(status="section_not_found", uri=uri, section=section)
            start = max(0, match - 120)
        size = max(256, min(int(max_chars), 20000))
        excerpt = content[start:start + size]
        next_offset = start + len(excerpt)
        return dict(status="expanded", uri=uri, title=row["title"],
                    kind=row["kind"], source=row["source"], level=level,
                    content=excerpt, offset=start, total_chars=len(content),
                    has_more=next_offset < len(content),
                    next_offset=next_offset if next_offset < len(content) else None)

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        terms = _WORD.findall(str(query))[:10]
        if not terms:
            return []
        limit = max(1, min(int(limit), 20))
        with closing(self._connect()) as conn:
            if self.fts_enabled:
                expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
                rows = conn.execute(
                    "SELECT a.uri, a.title, a.kind, a.source, "
                    "snippet(context_artifact_fts, 2, '[', ']', '...', 20) AS excerpt "
                    "FROM context_artifact_fts "
                    "JOIN context_artifacts a ON a.rowid=context_artifact_fts.rowid "
                    "WHERE context_artifact_fts MATCH ? AND a.scope=? AND a.expires_at>? "
                    "ORDER BY bm25(context_artifact_fts) LIMIT ?",
                    (expr, self.scope, time.time(), limit),
                ).fetchall()
            else:
                pattern = "%" + terms[0] + "%"
                rows = conn.execute(
                    "SELECT uri,title,kind,source,substr(content,1,500) AS excerpt "
                    "FROM context_artifacts WHERE scope=? AND expires_at>? "
                    "AND (title LIKE ? OR content LIKE ?) LIMIT ?",
                    (self.scope, time.time(), pattern, pattern, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def record_usage(self, tool_name: str, raw_chars: int, returned_chars: int) -> None:
        """Record observed character savings; token savings require provider usage."""
        if raw_chars < 0 or returned_chars < 0:
            raise ValueError("negative usage")
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO context_usage(scope,tool_name,raw_chars,returned_chars,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (self.scope, str(tool_name)[:120], int(raw_chars),
                     int(returned_chars), time.time()),
                )

    def usage_stats(self) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT count(*) calls, coalesce(sum(raw_chars),0) raw_chars, "
                "coalesce(sum(returned_chars),0) returned_chars "
                "FROM context_usage WHERE scope=?", (self.scope,),
            ).fetchone()
        raw = row["raw_chars"]
        returned = row["returned_chars"]
        return dict(calls=row["calls"], raw_chars=raw, returned_chars=returned,
                    chars_avoided=max(0, raw - returned),
                    reduction_ratio=round(max(0, raw - returned) / raw, 4) if raw else 0.0,
                    note="Character reduction only; not measured LLM token or cost savings.")

    def save_checkpoint(self, session_id: str, payload: dict[str, Any]) -> int:
        if not session_id or len(session_id) > 256:
            raise ValueError("session_id required (max 256 chars)")
        if not isinstance(payload, dict):
            raise ValueError("checkpoint payload must be an object")
        document = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(document) > 32000:
            raise ValueError("checkpoint exceeds 32K chars")
        with closing(self._connect()) as conn:
            with conn:
                cur = conn.execute(
                    "INSERT INTO context_checkpoints(scope,session_id,created_at,payload) "
                    "VALUES(?,?,?,?)",
                    (self.scope, session_id, time.time(), document),
                )
                checkpoint_id = int(cur.lastrowid)
                # Retention (B-d): TTL expiry plus keep-newest-N per session, so
                # the table cannot grow without bound while the feature is on.
                conn.execute(
                    "DELETE FROM context_checkpoints "
                    "WHERE scope=? AND session_id=? AND (created_at < ? OR id NOT IN ("
                    "  SELECT id FROM context_checkpoints "
                    "  WHERE scope=? AND session_id=? ORDER BY id DESC LIMIT ?))",
                    (self.scope, session_id, time.time() - _CHECKPOINT_TTL_SECONDS,
                     self.scope, session_id, _CHECKPOINT_KEEP_PER_SESSION),
                )
                return checkpoint_id

    def latest_checkpoint(self, session_id: str) -> Optional[dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id,created_at,payload FROM context_checkpoints "
                "WHERE scope=? AND session_id=? ORDER BY id DESC LIMIT 1",
                (self.scope, session_id),
            ).fetchone()
        if row is None:
            return None
        return dict(id=row["id"], created_at=row["created_at"],
                    session_id=session_id, payload=json.loads(row["payload"]))

    def prune(self) -> int:
        """Delete expired artifacts and their search rows; TTL-expired checkpoints too (B-d)."""
        with closing(self._connect()) as conn:
            with conn:
                rows = conn.execute(
                    "SELECT rowid FROM context_artifacts WHERE expires_at<=?",
                    (time.time(),),
                ).fetchall()
                if self.fts_enabled:
                    conn.executemany(
                        "DELETE FROM context_artifact_fts WHERE rowid=?",
                        [(row[0],) for row in rows],
                    )
                conn.execute("DELETE FROM context_artifacts WHERE expires_at<=?", (time.time(),))
                conn.execute(
                    "DELETE FROM context_checkpoints WHERE created_at < ?",
                    (time.time() - _CHECKPOINT_TTL_SECONDS,),
                )
        return len(rows)
