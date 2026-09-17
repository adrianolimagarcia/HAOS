"""Persistent, versioned vector index for canonical MemoryRecord IDs."""
from __future__ import annotations
import json
import math
import sqlite3
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

class SQLiteVectorIndex:
    """Stores embeddings by canonical record ID and model version.

    The index never stores promptable text and only returns IDs; HybridMemoryRetriever
    resolves those IDs again through the canonical store and ACL policy.
    """

    def __init__(self, path: str | Path, model_version: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.model_version = model_version
        self.db = sqlite3.connect(str(self.path))
        self.db.execute("CREATE TABLE IF NOT EXISTS memory_vectors (record_id TEXT NOT NULL, model_version TEXT NOT NULL, dimensions INTEGER NOT NULL, vector_json TEXT NOT NULL, PRIMARY KEY(record_id, model_version))")

    def close(self) -> None:
        self.db.close()

    def upsert(self, record_id: str, vector: Sequence[float]) -> None:
        if not vector:
            raise ValueError("embedding must not be empty")
        values = tuple(float(value) for value in vector)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding contains a non-finite value")
        self.db.execute("INSERT OR REPLACE INTO memory_vectors VALUES (?,?,?,?)", (record_id, self.model_version, len(values), json.dumps(values)))
        self.db.commit()

    def search(self, query_vector: Sequence[float], limit: int = 20) -> List[str]:
        query = tuple(float(value) for value in query_vector)
        if not query:
            return []
        qnorm = math.sqrt(sum(value * value for value in query))
        if not qnorm:
            return []
        scored: List[Tuple[float, str]] = []
        for record_id, dimensions, raw in self.db.execute("SELECT record_id, dimensions, vector_json FROM memory_vectors WHERE model_version=?", (self.model_version,)):
            vector = tuple(json.loads(raw))
            if dimensions != len(query):
                continue
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                scored.append((sum(a * b for a, b in zip(query, vector)) / (qnorm * norm), record_id))
        return [record_id for _, record_id in sorted(scored, reverse=True)[:limit]]
