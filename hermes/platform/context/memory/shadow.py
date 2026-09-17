"""Shadow-mode retrieval: compare the new pipeline against the legacy one.

During a cutover the only safe way to earn trust in a new retrieval path is to
run both and *serve the old one*. This module makes that structural rather than
conventional:

* :meth:`ShadowRetriever.prefetch` returns the legacy context **verbatim**. The
  candidate path's output is never returned, not even as a fallback — if it were,
  a bug in the new path would silently change what the model sees, which is
  exactly what shadow mode exists to prevent.
* Every candidate failure is swallowed and counted. Shadow mode must not be able
  to break a turn.
* The comparison is recorded, not acted on: recall difference (which record IDs
  each side found), estimated tokens, latency, duplicate logical records, and
  scope violations (candidate records whose scope the caller was not authorized
  for).

Token counts are an *estimate* (``chars / 4``). The point is a stable relative
comparison between two contexts, not an exact billing figure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hermes.platform.context.memory.access import MemoryAccessContext
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.metrics import MemoryFabricMetrics
from hermes.platform.context.memory.retrieval import HybridMemoryRetriever

logger = logging.getLogger(__name__)

# Deliberately crude and deliberately documented: a real tokenizer would make the
# comparison depend on a model choice the shadow run is not testing.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return estimate_tokens_from_chars(len(text))


def estimate_tokens_from_chars(chars: int) -> int:
    return (max(0, chars) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


@dataclass
class ShadowComparison:
    """One query observed on both paths."""

    query: str
    legacy_ids: Tuple[str, ...]
    candidate_ids: Tuple[str, ...]
    legacy_chars: int
    candidate_chars: int
    legacy_latency_ms: float
    candidate_latency_ms: float
    duplicate_records: int = 0
    scope_violations: Tuple[str, ...] = ()
    candidate_error: str = ""

    @property
    def only_legacy(self) -> Tuple[str, ...]:
        return tuple(record_id for record_id in self.legacy_ids if record_id not in set(self.candidate_ids))

    @property
    def only_candidate(self) -> Tuple[str, ...]:
        return tuple(record_id for record_id in self.candidate_ids if record_id not in set(self.legacy_ids))

    @property
    def recall_delta(self) -> int:
        """Net records the candidate path found that the legacy path did not."""
        return len(self.only_candidate) - len(self.only_legacy)

    @property
    def token_delta(self) -> int:
        return estimate_tokens_from_chars(self.candidate_chars) - estimate_tokens_from_chars(self.legacy_chars)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "legacy_ids": list(self.legacy_ids),
            "candidate_ids": list(self.candidate_ids),
            "only_legacy": list(self.only_legacy),
            "only_candidate": list(self.only_candidate),
            "recall_delta": self.recall_delta,
            "legacy_chars": self.legacy_chars,
            "candidate_chars": self.candidate_chars,
            "legacy_latency_ms": round(self.legacy_latency_ms, 3),
            "candidate_latency_ms": round(self.candidate_latency_ms, 3),
            "duplicate_records": self.duplicate_records,
            "scope_violations": list(self.scope_violations),
            "candidate_error": self.candidate_error,
        }


@dataclass
class ShadowReport:
    """Aggregate of every comparison observed so far."""

    runs: int = 0
    candidate_errors: int = 0
    total_recall_delta: int = 0
    total_legacy_tokens: int = 0
    total_candidate_tokens: int = 0
    total_duplicate_records: int = 0
    total_scope_violations: int = 0
    legacy_latency_ms: List[float] = field(default_factory=list)
    candidate_latency_ms: List[float] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        def _mean(values: Sequence[float]) -> float:
            return round(sum(values) / len(values), 3) if values else 0.0

        return {
            "runs": self.runs,
            "candidate_errors": self.candidate_errors,
            "total_recall_delta": self.total_recall_delta,
            "total_legacy_tokens": self.total_legacy_tokens,
            "total_candidate_tokens": self.total_candidate_tokens,
            "token_delta": self.total_candidate_tokens - self.total_legacy_tokens,
            "total_duplicate_records": self.total_duplicate_records,
            "total_scope_violations": self.total_scope_violations,
            "mean_legacy_latency_ms": _mean(self.legacy_latency_ms),
            "mean_candidate_latency_ms": _mean(self.candidate_latency_ms),
            "max_candidate_latency_ms": round(max(self.candidate_latency_ms), 3) if self.candidate_latency_ms else 0.0,
        }


class LegacyFederatedRetriever:
    """The pre-canonical retrieval path: Obsidian + DecisionStore + GraphRAG.

    This is the L3/L4/L5 federation exactly as it behaved before the canonical
    journal existed — three independent stores concatenated into one context
    block, with no shared identity, no ACL and no dedupe. Shadow mode needs it as
    a reference point, and keeping it as a thin adapter over the still-present
    adapters means the comparison measures the *pipeline*, not a reimplementation
    of it.
    """

    def __init__(self, obsidian: Any, decisions: Any, graphrag: Any, budget_chars: int = 6000) -> None:
        self.obsidian = obsidian
        self.decisions = decisions
        self.graphrag = graphrag
        self.budget_chars = budget_chars

    @staticmethod
    def _render(items: Sequence[Any]) -> List[str]:
        blocks: List[str] = []
        for item in items:
            content = getattr(item, "content", "") or ""
            title = getattr(item, "title", "") or ""
            uri = getattr(item, "source_uri", "") or ""
            blocks.append("--- %s (%s)\n%s" % (title or uri or "memory", uri, content))
        return blocks

    def __call__(self, query: str, access: MemoryAccessContext) -> str:
        """Concatenate every legacy source, truncated to the same char budget.

        The access context is accepted and deliberately unused: the legacy path
        had no notion of scope. That asymmetry is the thing shadow mode is meant
        to surface, not to hide.
        """
        blocks: List[str] = []
        for source in (self.obsidian, self.decisions, self.graphrag):
            if source is None:
                continue
            try:
                blocks.extend(self._render(source.retrieve(query=query)))
            except Exception:  # noqa: BLE001
                logger.warning("Legacy source %r failed during shadow retrieval", source, exc_info=True)
        joined = "\n\n".join(blocks)
        return joined[: self.budget_chars]


class ShadowRetriever:
    """Serves legacy context; observes the candidate path alongside it."""

    def __init__(
        self,
        *,
        legacy: Callable[[str, MemoryAccessContext], str],
        canonical_store: CanonicalMemoryStore,
        vector_search: Optional[Callable[[str, Sequence[str], int], Sequence[str]]] = None,
        metrics: Optional[MemoryFabricMetrics] = None,
        limit: int = 8,
        budget_chars: int = 6000,
    ) -> None:
        self.legacy = legacy
        self.store = canonical_store
        self.retriever = HybridMemoryRetriever(canonical_store, vector_search, metrics)
        self.metrics = metrics or MemoryFabricMetrics()
        self.limit = limit
        self.budget_chars = budget_chars
        self.comparisons: List[ShadowComparison] = []

    def _candidate_ids(self, query: str, access: MemoryAccessContext) -> Tuple[List[str], int, Tuple[str, ...]]:
        scopes = access.allowed_scopes()
        hits = self.retriever.retrieve(query, scopes, self.limit, self.budget_chars, access)
        ids = [hit.record.record_id for hit in hits]
        logical = [hit.record.logical_id for hit in hits]
        duplicates = len(logical) - len(set(logical))
        violations = tuple(
            hit.record.record_id
            for hit in hits
            if not access.can_read(hit.record.scope, hit.record.metadata)
        )
        return ids, duplicates, violations

    def prefetch(self, query: str, access: MemoryAccessContext) -> str:
        """Return the LEGACY context; record what the candidate path would have said."""
        started = time.perf_counter()
        legacy_context = self.legacy(query, access)
        legacy_latency = (time.perf_counter() - started) * 1000.0

        candidate_ids: List[str] = []
        duplicates = 0
        violations: Tuple[str, ...] = ()
        candidate_context = ""
        error = ""
        started = time.perf_counter()
        try:
            candidate_ids, duplicates, violations = self._candidate_ids(query, access)
            candidate_context = self.retriever.format_context(
                query, access.allowed_scopes(), self.limit, self.budget_chars, access
            )
        except Exception as exc:  # noqa: BLE001
            # Shadow mode must never be able to break a turn.
            error = repr(exc)
            self.metrics.increment("shadow.candidate_errors")
            logger.warning("Shadow candidate retrieval failed for %r", query, exc_info=True)
        candidate_latency = (time.perf_counter() - started) * 1000.0

        legacy_ids = self._legacy_ids(legacy_context)
        comparison = ShadowComparison(
            query=query,
            legacy_ids=legacy_ids,
            candidate_ids=tuple(candidate_ids),
            legacy_chars=len(legacy_context),
            candidate_chars=len(candidate_context),
            legacy_latency_ms=legacy_latency,
            candidate_latency_ms=candidate_latency,
            duplicate_records=duplicates,
            scope_violations=violations,
            candidate_error=error,
        )
        self.comparisons.append(comparison)

        self.metrics.increment("shadow.runs")
        self.metrics.increment("shadow.recall_delta", comparison.recall_delta)
        self.metrics.increment("shadow.legacy_tokens", estimate_tokens(legacy_context))
        self.metrics.increment("shadow.candidate_tokens", estimate_tokens(candidate_context))
        if duplicates:
            self.metrics.increment("shadow.duplicate_records", duplicates)
        if violations:
            self.metrics.increment("shadow.scope_violations", len(violations))

        return legacy_context

    @staticmethod
    def _legacy_ids(legacy_context: str) -> Tuple[str, ...]:
        """Canonical record IDs the legacy context referenced, if any.

        The legacy path may not emit IDs at all; then the comparison is purely
        about size/latency, and ``legacy_ids`` is empty rather than invented.
        """
        ids: List[str] = []
        for line in legacy_context.splitlines():
            marker = "[memory:"
            if marker in line:
                fragment = line.split(marker, 1)[1].split(" ", 1)[0].rstrip("]")
                if fragment:
                    ids.append(fragment)
        return tuple(dict.fromkeys(ids))

    def report(self) -> ShadowReport:
        report = ShadowReport()
        for comparison in self.comparisons:
            report.runs += 1
            if comparison.candidate_error:
                report.candidate_errors += 1
            report.total_recall_delta += comparison.recall_delta
            report.total_legacy_tokens += estimate_tokens_from_chars(comparison.legacy_chars)
            report.total_candidate_tokens += estimate_tokens_from_chars(comparison.candidate_chars)
            report.total_duplicate_records += comparison.duplicate_records
            report.total_scope_violations += len(comparison.scope_violations)
            report.legacy_latency_ms.append(comparison.legacy_latency_ms)
            report.candidate_latency_ms.append(comparison.candidate_latency_ms)
        return report
