"""Shadow-mode retrieval and the ordered feature-flag cutover.

Two contracts are pinned here.

**Shadow mode serves the legacy context.** Not "prefers" — returns it verbatim,
even when the candidate path raises. A shadow rollout whose candidate output can
leak into the prompt is not a shadow rollout, and the difference is exactly the
kind of thing that only shows up in production.

**Every flag's OFF state is a working path.** A flag whose disabled branch does
nothing cannot be rolled back, so each stage is exercised in both positions, and
the ordering (prerequisites + cascade rollback) is enforced rather than trusted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.platform.context.memory.access import MemoryAccessContext
from hermes.platform.context.memory.embedding import HashingEmbedder
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.context.memory.flags import CUTOVER_ORDER, FlagError, MemoryFeatureFlags
from hermes.platform.context.memory.shadow import ShadowRetriever, estimate_tokens

DECISION = "DECISION: the canonical journal is the only source of truth for memory."


def _alice() -> MemoryAccessContext:
    return MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))


def _coordinator(tmp_path: Path, **kwargs) -> FederatedMemoryCoordinator:
    return FederatedMemoryCoordinator(vault_path=tmp_path / "vault", **kwargs)


# --------------------------------------------------------------------------
# Shadow mode
# --------------------------------------------------------------------------


def test_shadow_serves_legacy_context_verbatim(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        access = _alice()
        coordinator.ingest_candidate_fact(
            fact=DECISION, scope="project", provenance="session://s", access_context=access,
        )
        shadow = ShadowRetriever(
            legacy=lambda query, access: "LEGACY-CONTEXT-BYTES",
            canonical_store=coordinator.canonical_store,
            vector_search=coordinator._vector_search,
            metrics=coordinator.metrics,
        )
        returned = shadow.prefetch("canonical journal", access)
        assert returned == "LEGACY-CONTEXT-BYTES"

        comparison = shadow.comparisons[-1]
        assert comparison.candidate_ids, "candidate path found nothing to compare"
        assert comparison.candidate_chars > 0
        assert comparison.legacy_chars == len("LEGACY-CONTEXT-BYTES")
        assert comparison.duplicate_records == 0
        assert comparison.scope_violations == ()
        assert comparison.candidate_error == ""
    finally:
        coordinator.close()


def test_shadow_never_leaks_the_candidate_path_when_it_fails(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project")
        shadow = ShadowRetriever(
            legacy=lambda query, access: "LEGACY-ONLY",
            canonical_store=coordinator.canonical_store,
            metrics=coordinator.metrics,
        )
        # Break the candidate path at its root.
        shadow.retriever.store = None  # type: ignore[assignment]
        assert shadow.prefetch("canonical journal", _alice()) == "LEGACY-ONLY"
        assert shadow.comparisons[-1].candidate_error
        assert shadow.report().candidate_errors == 1
        assert coordinator.metrics.counter("shadow.candidate_errors") == 1
    finally:
        coordinator.close()


def test_shadow_reports_recall_tokens_latency_and_duplicates(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        access = _alice()
        coordinator.ingest_candidate_fact(fact="Retention window is 30 days.", scope="project", access_context=access)
        coordinator.ingest_candidate_fact(fact="Deploys use Kubernetes.", scope="project", access_context=access)
        shadow = ShadowRetriever(
            legacy=lambda query, access: "",
            canonical_store=coordinator.canonical_store,
            vector_search=coordinator._vector_search,
            metrics=coordinator.metrics,
        )
        shadow.prefetch("Retention window", access)
        shadow.prefetch("Deploys use Kubernetes", access)

        report = shadow.report()
        assert report.runs == 2
        # The legacy path returned nothing, so every candidate hit is a recall
        # delta — the contract, independent of how many hits FTS returns.
        candidate_hits = sum(len(comparison.candidate_ids) for comparison in shadow.comparisons)
        assert candidate_hits > 0
        assert report.total_recall_delta == candidate_hits
        assert all(comparison.only_legacy == () for comparison in shadow.comparisons)
        assert report.total_candidate_tokens > 0
        assert report.total_legacy_tokens == 0
        assert len(report.candidate_latency_ms) == 2
        assert len(report.legacy_latency_ms) == 2
        payload = report.as_dict()
        assert payload["runs"] == 2
        assert payload["mean_candidate_latency_ms"] >= 0
        assert payload["total_recall_delta"] == candidate_hits
        assert payload["total_duplicate_records"] == 0
        assert payload["total_scope_violations"] == 0

        snapshot = coordinator.fabric_metrics()
        assert snapshot["counters"]["shadow.runs"] == 2
        assert snapshot["counters"]["shadow.recall_delta"] == candidate_hits
        assert estimate_tokens("abcdefgh") == 2
    finally:
        coordinator.close()


def test_shadow_records_scope_violations_from_the_canonical_store(tmp_path: Path) -> None:
    """A candidate hit the caller may not read is a violation, and is reported."""
    coordinator = _coordinator(tmp_path)
    try:
        coordinator.ingest_candidate_fact(
            fact="Private credential rotation.", scope="private",
            access_context=MemoryAccessContext("alice"),
        )
        bob = MemoryAccessContext("bob")
        shadow = coordinator.shadow_retriever()
        assert shadow.metrics is coordinator.metrics, "shadow must report into the fabric registry"
        # Force the candidate path to consider a scope Bob is not entitled to.
        hits = shadow.retriever.retrieve("credential", ("private",), access=bob)
        assert hits == []
        # The retriever's own ACL filter is what prevents the violation, and the
        # drop is counted so an empty result is distinguishable from an empty store.
        assert coordinator.metrics.counter("dropped_by_acl") >= 1
    finally:
        coordinator.close()


# --------------------------------------------------------------------------
# Feature flags
# --------------------------------------------------------------------------


def test_flag_order_and_prerequisites_are_enforced() -> None:
    flags = MemoryFeatureFlags(enabled_flags=set())
    assert flags.active() == []
    with pytest.raises(FlagError):
        flags.enable("canonical_fts")
    assert flags.advance(1) == ["canonical_writes"]
    assert flags.position() == 1
    with pytest.raises(FlagError):
        flags.enable("vector_rrf")
    assert flags.advance(2) == ["projections_via_outbox", "canonical_fts"]
    assert flags.position() == 3
    with pytest.raises(FlagError):
        flags.enabled("not_a_flag")


def test_rollback_cascades_to_dependent_stages() -> None:
    flags = MemoryFeatureFlags()
    assert flags.position() == len(CUTOVER_ORDER)
    turned_off = flags.disable("canonical_fts")
    assert turned_off == ["canonical_fts", "vector_rrf", "legacy_writers_disabled", "legacy_readers_disabled"]
    assert flags.snapshot()["canonical_writes"] is True
    assert flags.snapshot()["vector_rrf"] is False
    # The dependent stages can be re-enabled in order.
    assert flags.advance(1) == ["canonical_fts"]


def test_rollback_is_immediate_and_reversible() -> None:
    flags = MemoryFeatureFlags()
    assert flags.rollback(1) == ["legacy_readers_disabled"]
    assert flags.rollback(1) == ["legacy_writers_disabled"]
    assert flags.rollback(2) == ["vector_rrf", "canonical_fts"]
    assert flags.active() == ["canonical_writes", "projections_via_outbox"]
    assert flags.advance(4) == ["canonical_fts", "vector_rrf", "legacy_writers_disabled", "legacy_readers_disabled"]
    flags.reset(enabled=False)
    assert flags.active() == []


def test_canonical_writes_off_uses_the_legacy_write_path(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        coordinator.flags.disable("canonical_writes")
        assert coordinator.flags.active() == []
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project", provenance="session://s")

        # No canonical record, but the projections were written directly.
        assert coordinator.canonical_store.list_records() == []
        notes = list((tmp_path / "vault").rglob("*.md"))
        assert notes, "the legacy path did not project to Obsidian"
        assert DECISION in notes[0].read_text(encoding="utf-8")
        assert coordinator.graphrag_store is not None
        assert coordinator.metrics.counter("legacy.writes") == 1
    finally:
        coordinator.close()


def test_projections_via_outbox_off_applies_inline_without_backlog(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        coordinator.flags.disable("projections_via_outbox")
        assert coordinator.flags.enabled("canonical_writes")
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project", provenance="session://s")

        assert coordinator.canonical_store.pending_projections() == 0, "inline mode left jobs behind"
        assert coordinator.vector_index.count() == 1
        notes = list((tmp_path / "vault").rglob("*.md"))
        assert notes and DECISION in notes[0].read_text(encoding="utf-8")
        assert len(coordinator.canonical_store.list_records()) == 1
    finally:
        coordinator.close()


def test_canonical_fts_off_serves_the_legacy_reader(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        access = _alice()
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project", access_context=access)
        provider = coordinator.memory_provider
        provider.initialize(session_id="s", memory_access_context=access)

        # Full cutover: the canonical reader answers.
        assert "canonical journal" in provider.prefetch("canonical journal", session_id="s")
        assert coordinator.metrics.counter("prefetch.canonical_reader") == 1
        assert coordinator.metrics.counter("prefetch.legacy_reader") == 0

        # Roll the reader back one stage: the legacy federation answers instead,
        # and the rollback also decommissions nothing else.
        coordinator.flags.disable("canonical_fts")
        assert coordinator.flags.enabled("canonical_writes")
        legacy = provider.prefetch("canonical journal", session_id="s")
        assert DECISION in legacy, "the legacy reader did not return the projected note"
        assert coordinator.metrics.counter("prefetch.legacy_reader") == 1
        assert coordinator.metrics.counter("prefetch.canonical_reader") == 1
    finally:
        coordinator.close()


def test_full_cutover_decommissions_the_legacy_paths(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        assert coordinator.flags.active() == list(CUTOVER_ORDER)
        with pytest.raises(FlagError):
            coordinator.legacy_context("anything", _alice())

        # Rolling back the reader stage cascades the decommission flag off, so the
        # legacy path is available again — that is what "immediate rollback" means.
        coordinator.flags.disable("canonical_fts")
        assert coordinator.legacy_context("anything", _alice()) == ""
        assert coordinator.flags.enabled("legacy_readers_disabled") is False
    finally:
        coordinator.close()


def test_vector_rrf_off_drops_the_vector_channel(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        access = _alice()
        coordinator.ingest_candidate_fact(fact=DECISION, scope="project", access_context=access)
        provider = coordinator.memory_provider
        provider.initialize(session_id="s", memory_access_context=access)

        assert "canonical journal" in provider.prefetch("canonical journal", session_id="s")
        assert coordinator.metrics.counter("channel_hits.vector") >= 1

        coordinator.flags.disable("vector_rrf")
        before = coordinator.metrics.counter("channel_hits.vector")
        assert "canonical journal" in provider.prefetch("canonical journal", session_id="s")
        assert coordinator.metrics.counter("channel_hits.vector") == before, "vector channel still ran"
    finally:
        coordinator.close()
