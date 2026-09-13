"""HAOS RAGFlow Engine (Deep Document Understanding, Breadcrumb Chunking & RRF Hybrid Search).

Inspired by infiniflow/ragflow:
1. Header Breadcrumb Chunking:
   Maintains hierarchical heading context (# A > ## B > ### C) across chunks,
   preventing "Garbage In, Garbage Out" in retrieval and preserving tables and code blocks.
2. Traceable Provenance Anchors:
   Every chunk carries an immutable reference anchor: `[ref: path/doc.md#L45-L68]`.
3. Reciprocal Rank Fusion (RRF):
   Deterministic rank-based score fusion across SQLite FTS5 (BM25) and lexical/tag matching.
4. Offline-first SQLite WAL storage:
   Zero external daemons (no Elasticsearch/Redis/MinIO required).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("haos.memory.ragflow")


@dataclass
class DocumentChunk:
    chunk_id: str
    doc_id: str
    doc_path: str
    breadcrumb: List[str]
    header_path: str
    content: str
    start_line: int
    end_line: int
    provenance_anchor: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def formatted_for_llm(self, include_anchor: bool = True) -> str:
        """Formats chunk for LLM prompt injection with breadcrumbs and provenance."""
        anchor_part = f" {self.provenance_anchor}" if include_anchor else ""
        header_part = f" | {self.header_path}" if self.header_path else ""
        return f"[Doc: {self.doc_path}{header_part}]{anchor_part}\n{self.content}"


class HeaderBreadcrumbChunker:
    """Hierarchical Markdown and Document Chunker.

    Preserves heading hierarchy, avoids splitting code blocks/tables,
    and attaches exact line-level provenance anchors.
    """

    def __init__(
        self,
        max_chars: int = 1200,
        overlap_chars: int = 150,
        min_chars: int = 60,
    ):
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        self.min_chars = min_chars

    def chunk_markdown(
        self,
        text: str,
        doc_path: str = "document.md",
        doc_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[DocumentChunk]:
        """Splits markdown text into hierarchical chunks with breadcrumbs."""
        if not text or not text.strip():
            return []

        doc_id = doc_id or str(uuid.uuid4().hex[:12])
        meta = metadata or {}
        lines = text.splitlines()

        chunks: List[DocumentChunk] = []
        header_stack: List[Tuple[int, str]] = []  # (level, title)

        current_lines: List[str] = []
        chunk_start_line = 1
        in_code_fence = False

        def get_header_path() -> str:
            if not header_stack:
                return ""
            return " > ".join(f"{'#' * lvl} {title}" for lvl, title in header_stack)

        def get_breadcrumb() -> List[str]:
            return [title for _, title in header_stack]

        def emit_current_chunk(end_line_num: int) -> None:
            nonlocal current_lines, chunk_start_line
            chunk_body = "\n".join(current_lines).strip()
            if not chunk_body:
                current_lines = []
                return

            cid = f"{doc_id}-{len(chunks) + 1:04d}"
            anchor = f"[ref: {doc_path}#L{chunk_start_line}-L{end_line_num}]"

            chunk = DocumentChunk(
                chunk_id=cid,
                doc_id=doc_id,
                doc_path=doc_path,
                breadcrumb=get_breadcrumb(),
                header_path=get_header_path(),
                content=chunk_body,
                start_line=chunk_start_line,
                end_line=end_line_num,
                provenance_anchor=anchor,
                metadata=dict(meta),
            )
            chunks.append(chunk)
            current_lines = []

        header_re = re.compile(r"^(#{1,6})\s+(.+)$")

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Track fenced code blocks (never break inside a code fence)
            if stripped.startswith("```"):
                in_code_fence = not in_code_fence

            # If not in code fence, check for Markdown headers
            if not in_code_fence:
                m = header_re.match(line)
                if m:
                    # New section encountered: flush previous accumulated content
                    if current_lines:
                        emit_current_chunk(idx - 1)

                    level = len(m.group(1))
                    title = m.group(2).strip()

                    # Pop headers deeper than or equal to current level
                    while header_stack and header_stack[-1][0] >= level:
                        header_stack.pop()
                    header_stack.append((level, title))

                    chunk_start_line = idx
                    current_lines.append(line)
                    continue

            current_lines.append(line)
            current_len = sum(len(l) + 1 for l in current_lines)

            # Split if chunk exceeded max_chars and we're not inside a code fence or table
            if current_len >= self.max_chars and not in_code_fence:
                # Avoid splitting mid-table
                if not stripped.startswith("|") or (idx < len(lines) and not lines[idx].strip().startswith("|")):
                    emit_current_chunk(idx)
                    chunk_start_line = idx + 1

        # Emit any trailing lines
        if current_lines:
            emit_current_chunk(len(lines))

        return chunks


class ReciprocalRankFusion:
    """Reciprocal Rank Fusion (RRF) for Hybrid Information Retrieval.

    Combines independent ranked lists without requiring score normalization:
        RRF_Score(d) = SUM_i ( weight_i / (k + rank_i(d)) )
    """

    @staticmethod
    def fuse(
        rankings: List[List[Tuple[str, float]]],
        k: int = 60,
        weights: Optional[List[float]] = None,
    ) -> List[Tuple[str, float]]:
        """Fuses multiple ranked lists of (item_id, original_score) into a single ordered list."""
        if not rankings:
            return []

        weights = weights or [1.0] * len(rankings)
        scores: Dict[str, float] = {}

        for r_idx, ranked_items in enumerate(rankings):
            w = weights[r_idx] if r_idx < len(weights) else 1.0
            for rank_pos, (item_id, _) in enumerate(ranked_items, start=1):
                rrf_val = w / (k + rank_pos)
                scores[item_id] = scores.get(item_id, 0.0) + rrf_val

        # Sort descending by fused score
        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_items


class RAGFlowStore:
    """SQLite WAL-backed RAG engine with FTS5 lexical search and RRF fusion."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            from hermes_constants import get_hermes_home
            home = Path(get_hermes_home())
            db_path = home / "memory" / "ragflow.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.chunker = HeaderBreadcrumbChunker()
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
                CREATE TABLE IF NOT EXISTS haos_rag_chunks (
                    id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    doc_path TEXT NOT NULL,
                    breadcrumb_json TEXT NOT NULL,
                    header_path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    provenance_anchor TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    metadata_json TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_doc ON haos_rag_chunks(doc_path);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_doc_id ON haos_rag_chunks(doc_id);")

            # Virtual table FTS5 for BM25 lexical ranking
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS haos_rag_fts USING fts5(
                    id UNINDEXED,
                    doc_path,
                    header_path,
                    content,
                    tokenize='unicode61'
                );
            """)
            conn.commit()

    def index_document(
        self,
        doc_path: str,
        text: str,
        doc_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Chunks and indexes a document into relational and FTS5 tables."""
        chunks = self.chunker.chunk_markdown(text, doc_path=doc_path, doc_id=doc_id, metadata=metadata)
        if not chunks:
            return 0

        with self._lock, self._get_connection() as conn:
            # Remove previous chunks for this document
            resolved_doc_id = chunks[0].doc_id
            conn.execute("DELETE FROM haos_rag_chunks WHERE doc_path = ? OR doc_id = ?;", (doc_path, resolved_doc_id))
            conn.execute("DELETE FROM haos_rag_fts WHERE doc_path = ?;", (doc_path,))

            for c in chunks:
                conn.execute("""
                    INSERT INTO haos_rag_chunks (
                        id, doc_id, doc_path, breadcrumb_json, header_path,
                        content, start_line, end_line, provenance_anchor, created_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    c.chunk_id,
                    c.doc_id,
                    c.doc_path,
                    json.dumps(c.breadcrumb),
                    c.header_path,
                    c.content,
                    c.start_line,
                    c.end_line,
                    c.provenance_anchor,
                    c.created_at,
                    json.dumps(c.metadata),
                ))
                conn.execute("""
                    INSERT INTO haos_rag_fts (id, doc_path, header_path, content)
                    VALUES (?, ?, ?, ?);
                """, (c.chunk_id, c.doc_path, c.header_path, c.content))

            conn.commit()

        return len(chunks)

    def index_file(self, file_path: Path | str) -> int:
        """Reads a local file and indexes it."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        return self.index_document(doc_path=str(path), text=text)

    def index_directory(self, dir_path: Path | str, glob_pattern: str = "**/*.md") -> Dict[str, int]:
        """Indexes all matching files in a directory."""
        root = Path(dir_path)
        indexed: Dict[str, int] = {}
        for p in root.glob(glob_pattern):
            if p.is_file():
                try:
                    count = self.index_file(p)
                    indexed[str(p)] = count
                except Exception as exc:
                    logger.warning("Failed indexing %s: %s", p, exc)
        return indexed

    @staticmethod
    def _sanitize_fts_query(query_str: str) -> str:
        """Sanitizes query string into valid FTS5 tokens."""
        tokens = re.findall(r"\b\w+\b", query_str)
        if not tokens:
            return ""
        # Quote each token to prevent FTS5 syntax exceptions
        return " OR ".join(f'"{t}"' for t in tokens)

    def hybrid_search(
        self,
        query_str: str,
        limit: int = 5,
        k_rrf: int = 60,
    ) -> List[DocumentChunk]:
        """Executes hybrid retrieval combining FTS5 BM25 and substring/token coverage with RRF."""
        clean_q = query_str.strip()
        if not clean_q:
            return []

        fts_query = self._sanitize_fts_query(clean_q)
        tokens = [t.lower() for t in re.findall(r"\b\w+\b", clean_q)]

        with self._lock, self._get_connection() as conn:
            # 1. FTS5 BM25 Ranking
            fts_ranked: List[Tuple[str, float]] = []
            if fts_query:
                try:
                    rows = conn.execute("""
                        SELECT id, bm25(haos_rag_fts) AS rank_score
                        FROM haos_rag_fts
                        WHERE haos_rag_fts MATCH ?
                        ORDER BY rank_score ASC
                        LIMIT ?;
                    """, (fts_query, limit * 3)).fetchall()
                    fts_ranked = [(r["id"], float(r["rank_score"])) for r in rows]
                except Exception as exc:
                    logger.warning("FTS5 query failed (%s): %s", fts_query, exc)

            # 2. Token Overlap & Semantic Breadcrumb Ranking
            all_chunks_rows = conn.execute("""
                SELECT id, doc_path, header_path, content FROM haos_rag_chunks
                ORDER BY created_at DESC LIMIT 200;
            """).fetchall()

            lexical_candidates: List[Tuple[str, float]] = []
            for r in all_chunks_rows:
                cid = r["id"]
                text_blob = f"{r['doc_path']} {r['header_path']} {r['content']}".lower()
                matches = sum(1 for t in tokens if t in text_blob)
                if matches > 0:
                    score = matches / max(len(tokens), 1)
                    # Boost exact query phrase
                    if clean_q.lower() in text_blob:
                        score += 1.0
                    lexical_candidates.append((cid, score))

            lexical_candidates.sort(key=lambda x: x[1], reverse=True)
            lexical_ranked = lexical_candidates[: limit * 3]

            # 3. Fuse with Reciprocal Rank Fusion
            fused = ReciprocalRankFusion.fuse([fts_ranked, lexical_ranked], k=k_rrf)
            top_ids = [cid for cid, _ in fused[:limit]]

            if not top_ids:
                return []

            # Fetch final chunks in fused order
            placeholders = ",".join("?" for _ in top_ids)
            chunk_rows = conn.execute(f"""
                SELECT * FROM haos_rag_chunks WHERE id IN ({placeholders});
            """, top_ids).fetchall()

            chunks_by_id = {
                r["id"]: DocumentChunk(
                    chunk_id=r["id"],
                    doc_id=r["doc_id"],
                    doc_path=r["doc_path"],
                    breadcrumb=json.loads(r["breadcrumb_json"] or "[]"),
                    header_path=r["header_path"],
                    content=r["content"],
                    start_line=int(r["start_line"]),
                    end_line=int(r["end_line"]),
                    provenance_anchor=r["provenance_anchor"],
                    metadata=json.loads(r["metadata_json"] or "{}"),
                    created_at=float(r["created_at"]),
                )
                for r in chunk_rows
            }

            return [chunks_by_id[cid] for cid in top_ids if cid in chunks_by_id]
