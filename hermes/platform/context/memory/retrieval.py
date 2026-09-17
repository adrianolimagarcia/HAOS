"""Scope-safe hybrid retrieval for the canonical Memory Fabric.

Graph and vector indexes are candidate generators only. Canonical records are
the sole promptable payload, which prevents a stale or over-broad projection
from bypassing scope and supersession policy.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore, MemoryRecord
from hermes.platform.context.memory.access import MemoryAccessContext

@dataclass(frozen=True)
class RetrievalHit:
    record: MemoryRecord
    score: float
    channels: Tuple[str, ...]

class HybridMemoryRetriever:
    """Fuses FTS and optional vector ranks with weighted reciprocal-rank fusion."""

    def __init__(self, store: CanonicalMemoryStore, vector_search: Optional[Callable[[str, Sequence[str], int], Sequence[str]]] = None, metrics: Optional[Any] = None) -> None:
        self.store = store
        self.vector_search = vector_search
        self.metrics = metrics

    def _count(self, name: str, amount: int = 1) -> None:
        if self.metrics is not None and amount:
            self.metrics.increment(name, amount)

    @staticmethod
    def _rrf(rank: int, weight: float, k: int = 60) -> float:
        return weight / float(k + rank)

    def retrieve(self, query: str, allowed_scopes: Sequence[str], limit: int = 8, budget_chars: int = 6000, access: Optional[MemoryAccessContext] = None) -> List[RetrievalHit]:
        scopes = tuple(dict.fromkeys(allowed_scopes))
        if not scopes or limit <= 0 or budget_chars <= 0:
            return []
        # FTS is both a candidate channel and the authority filter.
        fts = self.store.search_fts(query, scopes, max(20, limit * 4))
        if access is not None:
            authorized = [record for record in fts if access.can_read(record.scope, record.metadata)]
            self._count("dropped_by_acl", len(fts) - len(authorized))
            fts = authorized
        ranks: Dict[str, float] = {}
        channels: Dict[str, List[str]] = {}
        by_id = {record.record_id: record for record in fts}
        for rank, record in enumerate(fts, 1):
            ranks[record.record_id] = ranks.get(record.record_id, 0.0) + self._rrf(rank, 1.0)
            channels.setdefault(record.record_id, []).append("fts")
            self._count("channel_hits.fts")
        if self.vector_search is not None:
            # Contract: vector search returns canonical record IDs only. Resolve
            # every ID again through the journal before emitting any content.
            vector_ids = tuple(self.vector_search(query, scopes, max(20, limit * 4)))
            resolved = self.store.active_by_ids(vector_ids, scopes)
            if access is not None:
                authorized = [record for record in resolved if access.can_read(record.scope, record.metadata)]
                self._count("dropped_by_acl", len(resolved) - len(authorized))
                resolved = authorized
            for record in resolved:
                by_id[record.record_id] = record
            for rank, record_id in enumerate(vector_ids, 1):
                if record_id in by_id:
                    ranks[record_id] = ranks.get(record_id, 0.0) + self._rrf(rank, 0.9)
                    channels.setdefault(record_id, []).append("vector")
                    self._count("channel_hits.vector")

        chosen: List[RetrievalHit] = []
        used = 0
        # Same logical memory is emitted once: latest active revision wins.
        seen_logical = set()
        for record_id, score in sorted(ranks.items(), key=lambda item: item[1], reverse=True):
            record = by_id[record_id]
            if record.logical_id in seen_logical:
                continue
            cost = len(record.content)
            if chosen and used + cost > budget_chars:
                self._count("dropped_by_budget")
                continue
            seen_logical.add(record.logical_id)
            used += cost
            chosen.append(RetrievalHit(record, score, tuple(channels[record_id])))
            if len(chosen) >= limit:
                break
        return chosen

    def format_context(self, query: str, allowed_scopes: Sequence[str], limit: int = 8, budget_chars: int = 6000, access: Optional[MemoryAccessContext] = None) -> str:
        blocks = []
        for hit in self.retrieve(query, allowed_scopes, limit, budget_chars, access):
            rec = hit.record
            provenance = ", ".join(str(p.get("uri", "")) for p in rec.provenance if p.get("uri"))
            blocks.append("[memory:%s scope=%s provenance=%s]\n%s" % (rec.record_id, rec.scope, provenance or "unknown", rec.content))
        return "\n\n".join(blocks)
