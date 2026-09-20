"""Persistent, versioned vector index for canonical MemoryRecord IDs.

The index never stores promptable text and only returns IDs; HybridMemoryRetriever
resolves those IDs again through the canonical store and ACL policy.

Three invariants make the projection rebuildable rather than a second source of
truth:

* **Vectors are keyed by ``(record_id, model_id)``.** Vectors from different
  models are never compared, and rows for an older model are retained so a
  rollback to the previous ``model_id`` is instant instead of a full rebuild.
* **The model's shape is recorded, not inferred.** ``memory_vector_models``
  stores dimensions/normalization/reindex policy, so a dimension change is
  detected at write time instead of silently producing garbage rankings.
* **Reindexing is driven by canonical records.** ``reindex`` takes the caller's
  ``(record_id, content)`` pairs, so a vector can only exist for a record the
  journal still considers active.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from hermes.platform.context.memory.embedding import (
    REINDEX_STRICT,
    Embedder,
    EmbeddingModel,
    validate_vector,
)


class SQLiteVectorIndex:
    """Stores embeddings by canonical record ID and model version."""

    def __init__(
        self,
        path: str | Path,
        model_version: str,
        *,
        dimensions: Optional[int] = None,
        normalize: bool = True,
        reindex_policy: str = "lazy",
    ) -> None:
        if not model_version or not model_version.strip():
            raise ValueError("model_version must not be empty")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.model_version = model_version
        self.normalize = normalize
        self.reindex_policy = reindex_policy
        self.dimensions = dimensions
        self.db = sqlite3.connect(str(self.path), timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS memory_vectors (record_id TEXT NOT NULL, model_version TEXT NOT NULL, "
            "dimensions INTEGER NOT NULL, vector_json TEXT NOT NULL, updated_at REAL NOT NULL DEFAULT 0, "
            "PRIMARY KEY(record_id, model_version))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS memory_vector_models (model_version TEXT PRIMARY KEY, dimensions INTEGER NOT NULL, "
            "normalize INTEGER NOT NULL, reindex_policy TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        self.db.commit()
        if dimensions is not None:
            self._register_model(dimensions)

    # -- model identity ------------------------------------------------------

    def _register_model(self, dimensions: int) -> None:
        self.db.execute(
            "INSERT INTO memory_vector_models VALUES (?,?,?,?,?) "
            "ON CONFLICT(model_version) DO UPDATE SET dimensions=excluded.dimensions, "
            "normalize=excluded.normalize, reindex_policy=excluded.reindex_policy, updated_at=excluded.updated_at",
            (self.model_version, int(dimensions), int(bool(self.normalize)), self.reindex_policy, time.time()),
        )
        self.db.commit()

    def registered_model(self) -> Optional[Tuple[str, int, bool, str]]:
        row = self.db.execute(
            "SELECT model_version, dimensions, normalize, reindex_policy FROM memory_vector_models WHERE model_version=?",
            (self.model_version,),
        ).fetchone()
        return (row[0], row[1], bool(row[2]), row[3]) if row else None

    def close(self) -> None:
        self.db.close()

    # -- writes --------------------------------------------------------------

    def upsert(self, record_id: str, vector: Sequence[float]) -> None:
        if not record_id:
            raise ValueError("record_id must not be empty")
        values = tuple(float(value) for value in vector)
        if not values:
            raise ValueError("embedding must not be empty")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding contains a non-finite value")
        if self.dimensions is None:
            # First write pins the shape for this model_version.
            self.dimensions = len(values)
            self._register_model(self.dimensions)
        elif len(values) != self.dimensions:
            raise ValueError(
                "embedding has %d dimensions, model %s is pinned to %d"
                % (len(values), self.model_version, self.dimensions)
            )
        self.db.execute(
            "INSERT OR REPLACE INTO memory_vectors VALUES (?,?,?,?,?)",
            (record_id, self.model_version, len(values), json.dumps(values), time.time()),
        )
        self.db.commit()

    def index_text(self, record_id: str, text: str, embedder: Embedder) -> None:
        """Embed one canonical record under ``embedder``'s model identity."""
        model = embedder.model
        if model.model_id != self.model_version:
            raise ValueError(
                "embedder model %s does not match index model %s"
                % (model.model_id, self.model_version)
            )
        self.upsert(record_id, validate_vector(embedder.embed(text), model))

    def delete(self, record_id: str) -> None:
        self.db.execute(
            "DELETE FROM memory_vectors WHERE record_id=? AND model_version=?",
            (record_id, self.model_version),
        )
        self.db.commit()

    # -- reindex policy ------------------------------------------------------

    def indexed_ids(self) -> set[str]:
        return {
            row[0]
            for row in self.db.execute(
                "SELECT record_id FROM memory_vectors WHERE model_version=?", (self.model_version,)
            )
        }

    def needs_reindex(self, record_ids: Iterable[str]) -> int:
        """How many of *record_ids* have no vector for the current model."""
        have = self.indexed_ids()
        return sum(1 for record_id in record_ids if record_id not in have)

    def reindex(self, records: Iterable[Tuple[str, str]], embedder: Embedder, *, force: bool = False) -> int:
        """Bring vectors for *records* up to the current model.

        ``records`` are ``(record_id, content)`` pairs read from the canonical
        journal, which is what keeps the index a projection: a record the journal
        no longer exposes can never be re-indexed into existence.
        """
        if embedder.model.model_id != self.model_version:
            raise ValueError(
                "embedder model %s does not match index model %s"
                % (embedder.model.model_id, self.model_version)
            )
        have = set() if force else self.indexed_ids()
        written = 0
        for record_id, content in records:
            if record_id in have:
                continue
            self.index_text(record_id, content, embedder)
            written += 1
        return written

    def model_identity(self) -> EmbeddingModel:
        return EmbeddingModel(
            model_id=self.model_version,
            dimensions=self.dimensions or 0,
            normalize=self.normalize,
            reindex_policy=self.reindex_policy,
        )

    # -- reads ---------------------------------------------------------------

    def search(self, query_vector: Sequence[float], limit: int = 20) -> List[str]:
        query = tuple(float(value) for value in query_vector)
        if not query:
            return []

        # Tentativa de Aceleração Nativa em Rust via haos-edge
        try:
            import urllib.request
            req_data = json.dumps({
                "query_vector": list(query),
                "model_version": self.model_version,
                "limit": limit,
                "db_path": str(self.path)
            }).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:8788/api/memory/vector-search",
                data=req_data,
                headers={"Content-Type": "application/json"}
            )
            # Timeout curto para fail-fast se o daemon Rust não estiver no ar
            with urllib.request.urlopen(req, timeout=0.15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("ok") and "results" in data:
                    return [item["record_id"] for item in data["results"][:limit]]
        except Exception:
            pass  # Fallback gracioso para o cálculo local em Python

        # Algoritmo Base Local (Fallback)
        qnorm = math.sqrt(sum(value * value for value in query))
        if not qnorm:
            return []
        scored: List[Tuple[float, str]] = []
        for record_id, dimensions, raw in self.db.execute(
            "SELECT record_id, dimensions, vector_json FROM memory_vectors WHERE model_version=?",
            (self.model_version,),
        ):
            vector = tuple(json.loads(raw))
            if dimensions != len(query):
                continue
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                scored.append((sum(a * b for a, b in zip(query, vector)) / (qnorm * norm), record_id))
        return [record_id for _, record_id in sorted(scored, reverse=True)[:limit]]

    def count(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM memory_vectors WHERE model_version=?", (self.model_version,)
        ).fetchone()[0]
