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

#: Below this a block is a provenance header with no payload left: it stops being memory and
#: becomes prompt noise. It is also the reservation held back for each not-yet-rendered hit.
_MIN_BLOCK_CHARS = 400


def _fit_block(block: str, allowance: int) -> str:
    """Truncate ``block`` to at most ``allowance`` chars, at a line boundary, and say so.

    The marker is part of the allowance, so the result is bounded by construction rather than by
    arithmetic that has to stay in sync with the marker's length.
    """
    if len(block) <= allowance:
        return block
    marker = "\n[…truncado: %d de %d chars]" % (allowance, len(block))
    room = max(0, allowance - len(marker))
    cut = block.rfind("\n", 0, room)
    if cut < room // 2:  # no usable line boundary near the cap: cut mid-line rather than lose it
        cut = room
    return block[:cut] + marker


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
        # O que um hit pode ocupar de fato no prompt é, no máximo, a cota justa de um hit: o
        # render corta o que passar disso. Cobrar ``len(content)`` integral fazia um registro de
        # 60k esgotar o budget e derrubar todos os menores depois dele — justamente os que
        # caberiam —, então um único documento grande apagava o resto do recall.
        share = max(1, budget_chars // max(1, limit))
        # Same logical memory is emitted once: latest active revision wins.
        seen_logical = set()
        for record_id, score in sorted(ranks.items(), key=lambda item: item[1], reverse=True):
            record = by_id[record_id]
            if record.logical_id in seen_logical:
                continue
            charge = min(len(record.content), share)
            if chosen and used + charge > budget_chars:
                self._count("dropped_by_budget")
                continue
            seen_logical.add(record.logical_id)
            used += charge
            chosen.append(RetrievalHit(record, score, tuple(channels[record_id])))
            if len(chosen) >= limit:
                break
        return chosen

    def format_context(self, query: str, allowed_scopes: Sequence[str], limit: int = 8, budget_chars: int = 6000, access: Optional[MemoryAccessContext] = None) -> str:
        """Render the recalled records as one prompt-ready block, hard-capped at ``budget_chars``.

        The cap is a contract, not a hint. This string is appended to the user message on every
        turn, so anything over it is paid for on every request — and ``retrieve``'s budget cannot
        enforce it: that budget bounds *candidate selection*, and deliberately admits the best hit
        whatever its size (``chosen and ...``) so a large-but-relevant record is not invisible.
        Enforcing the cap therefore has to happen here, on the rendered text.

        A record too large for its share is truncated rather than dropped, with the truncation
        marked in the text: silently cutting memory would let the model read a fragment as if it
        were the whole record, and dropping it outright would hide the most relevant hit.
        """
        hits = self.retrieve(query, allowed_scopes, limit, budget_chars, access)
        if not hits:
            return ""
        blocks = [self._render(hit) for hit in hits]
        # Never return nothing for a budget that is merely small: the floor shrinks with it.
        floor = min(_MIN_BLOCK_CHARS, budget_chars)
        rendered: List[str] = []
        used = 0
        for index, block in enumerate(blocks):
            remaining = budget_chars - used
            if remaining < floor:
                self._count("dropped_by_budget", len(blocks) - index)
                break
            # Reserve the floor for every later hit, so one large record cannot starve the rest:
            # a budget that always returns a single document is not a recall budget.
            reserve = floor * (len(blocks) - index - 1)
            allowance = min(len(block), max(floor, remaining - reserve))
            fitted = _fit_block(block, allowance)
            rendered.append(fitted)
            used += len(fitted) + 2  # the "\n\n" joiner; over-counts by 2 on the last block
            if len(fitted) < len(block):
                self._count("truncated_blocks")
        return "\n\n".join(rendered)

    @staticmethod
    def _render(hit: RetrievalHit) -> str:
        rec = hit.record
        provenance = ", ".join(str(p.get("uri", "")) for p in rec.provenance if p.get("uri"))
        return "[memory:%s scope=%s provenance=%s]\n%s" % (rec.record_id, rec.scope, provenance or "unknown", rec.content)
