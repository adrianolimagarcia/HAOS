"""Observability and chaos harness for the Memory Fabric.

Two different questions are answered here.

**Observability** — can an operator tell "the fabric is fine and there was
nothing to recall" apart from "the fabric silently stopped working"? That needs
backlog, retries, expired leases, per-projection latency, dedupe rate, hits per
channel, and — most easily forgotten — the contexts that were *dropped*, by
budget or by ACL. Each is asserted to move in the right direction, not merely to
exist.

**Chaos** — when a dependency dies, does the fabric degrade in a defined way?
SQLite closed under a live session, a failing embedder, a failing GraphRAG, and a
contended journal. The invariant under all of them: no enqueued work is lost, the
other sinks keep draining, and a turn still gets a context (possibly empty)
rather than an exception.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes.platform.context.memory.access import MemoryAccessContext
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.embedding import EmbeddingModel, HashingEmbedder
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.context.memory.graphrag_store import GraphRAGStore
from hermes.platform.context.memory.metrics import MemoryFabricMetrics, Timer
from hermes.platform.context.memory.vector_index import SQLiteVectorIndex

DECISION = "DECISION: projections are derived state and never write canonical rows."
OTHER = "DECISION: the outbox owns retries, not the caller."


class BrokenEmbedder:
    """An embedder that is reachable but always fails — the realistic outage."""

    def __init__(self) -> None:
        self.model = EmbeddingModel("broken-test-8", 8)
        self.calls = 0

    def embed(self, text: str):
        self.calls += 1
        raise RuntimeError("embedder backend unavailable")


def _alice() -> MemoryAccessContext:
    return MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))


def _coordinator(tmp_path: Path, **kwargs) -> FederatedMemoryCoordinator:
    return FederatedMemoryCoordinator(vault_path=tmp_path / "vault", **kwargs)


# --------------------------------------------------------------------------
# Observability
# --------------------------------------------------------------------------


def test_metrics_registry_tracks_counters_timings_and_rates() -> None:
    metrics = MemoryFabricMetrics()
    metrics.increment("outbox_retries")
    metrics.increment("outbox_retries", 2)
    metrics.increment("dedupe_hits", 3)
    metrics.increment("dedupe_misses", 1)
    metrics.increment("channel_hits.fts", 2)
    metrics.increment("channel_hits.vector", 1)
    with Timer(metrics, "projection.obsidian_seconds"):
        pass

    assert metrics.counter("outbox_retries") == 3
    assert metrics.counter("never_recorded") == 0
    assert metrics.dedupe_rate() == 0.75
    assert metrics.channel_hits() == {"fts": 2, "vector": 1}
    timing = metrics.timing("projection.obsidian_seconds")
    assert timing.count == 1 and timing.total_seconds >= 0
    snapshot = metrics.snapshot(backlog={"graphrag": 4})
    assert snapshot["outbox_backlog"] == {"graphrag": 4}
    assert snapshot["timings"]["projection.obsidian_seconds"]["count"] == 1


def test_dedupe_rate_and_projection_latency_are_measured(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        access = _alice()
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project", access_context=access)
        coordinator.ingest_candidate_fact(fact=OTHER, scope="project", access_context=access)
        # Same content again: the fabric must recognise it as a duplicate.
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project", access_context=access)

        metrics = coordinator.metrics
        assert metrics.counter("dedupe_hits") == 1
        assert metrics.counter("dedupe_misses") == 2
        assert metrics.dedupe_rate() == pytest.approx(1 / 3, abs=1e-6)

        for projection in CanonicalMemoryStore.PROJECTIONS:
            timing = metrics.timing("projection.%s_seconds" % projection)
            assert timing.count >= 1, "no latency recorded for %s" % projection
    finally:
        coordinator.close()


def test_channel_hits_and_acl_drops_are_distinguishable_from_an_empty_store(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        access = _alice()
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project", access_context=access)
        provider = coordinator.memory_provider
        provider.initialize(session_id="alice", memory_access_context=access)
        assert DECISION in provider.prefetch("projections derived state", session_id="alice")

        metrics = coordinator.metrics
        assert metrics.counter("channel_hits.fts") >= 1
        assert metrics.counter("channel_hits.vector") >= 1

        # Bob shares no scope with Alice: his query returns nothing AND the drop is
        # recorded, so this is not confusable with "the store is empty".
        bob = MemoryAccessContext("bob", frozenset({"team-b"}), frozenset({"project-b"}))
        provider.initialize(session_id="bob", memory_access_context=bob)
        before = metrics.counter("dropped_by_acl")
        assert provider.prefetch("projections derived state", session_id="bob") == ""
        assert metrics.counter("dropped_by_acl") > before
    finally:
        coordinator.close()


def test_budget_drops_are_counted(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        access = _alice()
        for index in range(6):
            # Each record is a large slice of the 5000-char prefetch budget, and
            # the bodies are genuinely distinct so the near-duplicate heuristic
            # (similarity >= 0.88) does not collapse them into one fact.
            body = " ".join("topic%d-term%d" % (index, term) for term in range(90))
            coordinator.ingest_candidate_fact(
                fact="DECISION: retention policy variant %d %s" % (index, body),
                scope="project",
                access_context=access,
            )
        assert len(coordinator.canonical_store.list_records()) == 6
        provider = coordinator.memory_provider
        provider.initialize(session_id="s", memory_access_context=access)
        provider.prefetch("retention policy variant", session_id="s")
        assert coordinator.metrics.counter("dropped_by_budget") >= 1, "budget truncation was invisible"
    finally:
        coordinator.close()


def test_live_lease_cannot_be_stolen(tmp_path: Path) -> None:
    """A claimed job stays claimed while its lease is valid.

    Split out from the recovery test and given a long lease on purpose: the original
    version claimed with a 0.01s lease and then immediately asserted a second claim
    returned nothing, so on a loaded runner more than 10ms elapsed between the two
    calls, the lease had already expired, and the assertion failed intermittently.
    """
    coordinator = _coordinator(tmp_path)
    try:
        store = coordinator.canonical_store
        store.append(content=DECISION, scope="project")

        assert len(store.claim("obsidian", "live-worker", 1, 60.0)) == 1
        assert store.claim("obsidian", "other-worker", 1, 60.0) == [], "the lease did not hold"
    finally:
        coordinator.close()


def test_backlog_and_expired_leases_are_reported(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        store = coordinator.canonical_store
        store.append(content=DECISION, scope="project")
        # Nothing drained: one record means one job per projection.
        assert store.pending_projections() == len(CanonicalMemoryStore.PROJECTIONS)
        backlog = coordinator.fabric_metrics()["outbox_backlog"]
        assert backlog == {projection: 1 for projection in CanonicalMemoryStore.PROJECTIONS}

        # A worker claims a job and dies holding it. The expired lease is expressed by
        # the lease timestamp itself (a negative lease) instead of by sleeping, so no
        # amount of runner load can turn this into a race.
        assert len(store.claim("obsidian", "dead-worker", 1, -1.0)) == 1

        # Recovery reclaims the abandoned lease and drains everything.
        assert coordinator.recover_projections() >= 1
        assert store.pending_projections() == 0
        assert coordinator.metrics.counter("expired_leases") >= 1
        assert coordinator.metrics.timing("projection.obsidian_seconds").count >= 1
    finally:
        coordinator.close()


def test_failed_projection_is_visible_and_retryable(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        store = coordinator.canonical_store

        def explode(record):
            raise RuntimeError("sink down")

        coordinator.projection_runner.projectors["graphrag"] = explode
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project")

        assert store.pending_by_projection() == {"graphrag": 1}
        assert store.failed_projections() == 1
        assert coordinator.metrics.counter("outbox_retries") >= 1

        coordinator.projection_runner.projectors["graphrag"] = coordinator._project_graphrag
        store.fail(
            "memory.changed:" + store.list_records()[0].record_id,
            "graphrag",
            "operator cleared the fault",
            retry_after=0.0,
        )
        assert coordinator.recover_projections() >= 1
        assert store.failed_projections() == 0
    finally:
        coordinator.close()


# --------------------------------------------------------------------------
# Chaos
# --------------------------------------------------------------------------


def test_embedder_outage_costs_the_vector_channel_not_recall(tmp_path: Path) -> None:
    broken = BrokenEmbedder()
    coordinator = _coordinator(tmp_path, embedder=broken)
    try:
        access = _alice()
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project", access_context=access)

        # The vector projection failed and stayed durable; the other three landed.
        assert coordinator.canonical_store.pending_by_projection().get("embeddings") == 1
        assert coordinator.metrics.counter("outbox_retries") >= 1
        notes = list((tmp_path / "vault").rglob("*.md"))
        assert notes and DECISION in notes[0].read_text(encoding="utf-8")

        # Retrieval degrades to FTS: a dead embedder costs the vector channel only.
        provider = coordinator.memory_provider
        provider.initialize(session_id="s", memory_access_context=access)
        assert DECISION in provider.prefetch("projections derived state", session_id="s")
        assert broken.calls >= 1

        # The job is retryable, not lost: swap in a working embedder and recover.
        coordinator.embedder = HashingEmbedder()
        coordinator.vector_index = SQLiteVectorIndex(
            tmp_path / "vectors-recovered.db",
            coordinator.embedder.model.model_id,
            dimensions=coordinator.embedder.model.dimensions,
            normalize=coordinator.embedder.model.normalize,
            reindex_policy=coordinator.embedder.model.reindex_policy,
        )
        coordinator.canonical_store.fail(
            "memory.changed:" + coordinator.canonical_store.list_records()[0].record_id,
            "embeddings",
            "operator replaced the embedder",
            retry_after=0.0,
        )
        coordinator.recover_projections()
        assert coordinator.canonical_store.pending_projections() == 0
        assert coordinator.vector_index.count() == 1
    finally:
        coordinator.close()


def test_graphrag_outage_does_not_block_the_other_projections(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, graphrag_store=GraphRAGStore(tmp_path / "graphrag.db"))
    try:
        def explode(record):
            raise RuntimeError("graphrag store is down")

        coordinator.projection_runner.projectors["graphrag"] = explode
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project")

        pending = coordinator.canonical_store.pending_by_projection()
        assert pending.get("graphrag") == 1
        for projection in ("obsidian", "decisions", "embeddings"):
            assert projection not in pending, "%s was blocked by the GraphRAG outage" % projection
        assert list((tmp_path / "vault").rglob("*.md")), "Obsidian was blocked by the GraphRAG outage"
        assert coordinator.metrics.counter("outbox_retries") >= 1

        # Restore the sink and replay: the job survived the outage.
        coordinator.projection_runner.projectors["graphrag"] = coordinator._project_graphrag
        coordinator.canonical_store.fail(
            "memory.changed:" + coordinator.canonical_store.list_records()[0].record_id,
            "graphrag",
            "operator restored the store",
            retry_after=0.0,
        )
        assert coordinator.recover_projections() >= 1
        assert coordinator.canonical_store.pending_projections() == 0
        assert coordinator.graphrag_store.list_entities()
    finally:
        coordinator.close()


def test_dead_journal_degrades_a_turn_and_fails_writes_loudly(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        access = _alice()
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project", access_context=access)
        provider = coordinator.memory_provider
        provider.initialize(session_id="s", memory_access_context=access)
        assert DECISION in provider.prefetch("projections", session_id="s")

        # The journal becomes unusable underneath a live session.
        coordinator.canonical_store.close()
        assert provider.prefetch("projections", session_id="s") == ""
        assert coordinator.metrics.counter("prefetch.failures") >= 1

        # A write against the dead journal fails loudly rather than silently
        # dropping the fact.
        with pytest.raises(sqlite3.ProgrammingError):
            coordinator.ingest_candidate_fact(fact=OTHER, scope="project", access_context=access)
    finally:
        coordinator.close()


def test_contended_journal_serializes_writers_instead_of_failing(tmp_path: Path) -> None:
    """WAL plus busy_timeout must make a second writer wait, not raise."""
    path = tmp_path / "journal.db"
    first = CanonicalMemoryStore(path)
    second = CanonicalMemoryStore(path)
    try:
        first.append(content=DECISION, scope="project")
        second.append(content=OTHER, scope="project")
        assert {record.content for record in first.list_records()} == {DECISION, OTHER}
        assert second.pending_projections() == 2 * len(CanonicalMemoryStore.PROJECTIONS)
    finally:
        first.close()
        second.close()
