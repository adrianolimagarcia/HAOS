"""Supersession policy: what counts as a change, and what must never be swallowed.

The cases are grouped by the direction of the failure, because the directions are not
equally bad:

* A missed supersession leaves a stale fact active — visible, queryable, fixable.
* A false supersession marks a legitimate fact ``superseded`` and removes it from recall
  with no trace.
* A contradiction absorbed as a *duplicate* is worse than either: the correction is
  discarded and the old, now-wrong value keeps being served.

The policy is therefore biased toward missing a change rather than inventing one, and
these tests pin that bias in both directions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator


def _coordinator(tmp_path: Path) -> FederatedMemoryCoordinator:
    return FederatedMemoryCoordinator(vault_path=tmp_path / "vault")


def _active_ids(coordinator: FederatedMemoryCoordinator) -> set:
    return {fact.id for fact in coordinator.list_facts(scope="project")}


@pytest.mark.parametrize(
    ("text_a", "text_b", "is_change", "why"),
    (
        ("the cache layer is enabled", "the cache layer is disabled", True,
         "opposite polarity: `enabled` and `disabled` used to sit in the same set and cancel out"),
        ("writes are allowed here", "writes are forbidden here", True, "antonym pair"),
        ("the field is required", "the field is optional", True, "antonym pair"),
        ("deploys do not use swarm", "deploys use swarm", True,
         "the negation is the only difference between the two statements"),
        ("there is no cache layer", "there is a cache layer", True,
         "`no` as the only difference"),
        ("retries cannot exceed five", "retries exceed five", True,
         "`cannot` as the only difference"),
        ("deploys migrated to kubernetes", "deploys use swarm", True, "explicit update wording"),
        ("the retry queue is enabled by default", "the retry queue stores 100 entries", False,
         "unrelated facts sharing two words: used to be a false supersession"),
        ("the legacy api is deprecated", "the legacy api returns 404", False,
         "a bare negation marker is not a contradiction on its own"),
        ("no retry queue is enabled by default", "the retry queue stores 100 entries", False,
         "a leading `no` does not make unrelated statements contradict"),
        ("the cache stores 100 entries", "the cache stores 200 entries", False,
         "a changed value is not a contradiction"),
    ),
)
def test_contradiction_contract(
    tmp_path: Path, text_a: str, text_b: str, is_change: bool, why: str
) -> None:
    """The detector's contract, in both directions.

    Exercised through a real coordinator (not a stub) because the write pipeline below
    depends on this exact object deciding.
    """
    coordinator = _coordinator(tmp_path)
    try:
        assert coordinator._is_contradiction_or_update(text_a, text_b) is is_change, why
    finally:
        coordinator.close()


def test_unrelated_fact_is_not_superseded(tmp_path: Path) -> None:
    """Regression: the first fact used to vanish from the active set."""
    coordinator = _coordinator(tmp_path)
    try:
        first = coordinator.ingest_candidate_fact(
            fact="the retry queue is enabled by default", scope="project",
        )
        second = coordinator.ingest_candidate_fact(
            fact="the retry queue stores 100 entries", scope="project",
        )
        active = _active_ids(coordinator)
        assert first.id in active, "an unrelated fact was superseded"
        assert second.id in active
        assert len(coordinator.canonical_store.list_records()) == 2
    finally:
        coordinator.close()


def test_contradiction_supersedes_instead_of_being_absorbed_as_a_duplicate(tmp_path: Path) -> None:
    """Regression: the correction used to be discarded and the old value kept.

    With a wording difference this small the near-duplicate threshold (0.88) fired first,
    so the second statement reinforced the first instead of replacing it — the journal
    ended up with a single record still saying ``disabled``.
    """
    coordinator = _coordinator(tmp_path)
    try:
        old = coordinator.ingest_candidate_fact(
            fact="the cache layer is disabled in production", scope="project",
        )
        new = coordinator.ingest_candidate_fact(
            fact="the cache layer is enabled in production", scope="project",
        )

        assert new.id != old.id, "the correction was absorbed as a duplicate"
        assert [fact.fact for fact in coordinator.list_facts(scope="project")] == [
            "the cache layer is enabled in production"
        ]

        # The old value is superseded, not deleted: the lineage stays walkable.
        lineage = {
            fact.id: fact
            for fact in coordinator.list_facts(scope="project", include_superseded=True)
        }
        assert lineage[old.id].superseded_by == new.id
        assert old.id in lineage[new.id].supersedes
        assert len(coordinator.canonical_store.list_records(include_superseded=True)) == 2
    finally:
        coordinator.close()


def test_exact_repetition_is_still_a_duplicate(tmp_path: Path) -> None:
    """The contradiction guard must not disable dedupe for genuine repeats."""
    coordinator = _coordinator(tmp_path)
    try:
        first = coordinator.ingest_candidate_fact(
            fact="the cache layer is disabled in production", scope="project",
        )
        again = coordinator.ingest_candidate_fact(
            fact="the cache layer is disabled in production", scope="project",
        )
        assert again.id == first.id
        assert len(coordinator.canonical_store.list_records()) == 1
    finally:
        coordinator.close()


def test_supersession_lineage_survives_restart(tmp_path: Path) -> None:
    """The lineage is a journal relation, so a restart must not resurrect the old fact."""
    coordinator = _coordinator(tmp_path)
    try:
        old = coordinator.ingest_candidate_fact(
            fact="the cache layer is disabled in production", scope="project",
        )
        new = coordinator.ingest_candidate_fact(
            fact="the cache layer is enabled in production", scope="project",
        )
    finally:
        coordinator.close()

    reopened = _coordinator(tmp_path)
    try:
        active = _active_ids(reopened)
        assert new.id in active
        assert old.id not in active
        lineage = {
            fact.id: fact
            for fact in reopened.list_facts(scope="project", include_superseded=True)
        }
        assert lineage[old.id].superseded_by == new.id
    finally:
        reopened.close()


# --------------------------------------------------------------------------
# Recoverability. The bias toward missing a change is only defensible if a wrong
# guess can be found and undone, so these two properties carry the same weight as
# the detector contract above.
# --------------------------------------------------------------------------


def test_automatic_supersessions_are_auditable(tmp_path: Path) -> None:
    """A heuristic supersession must record *why*, and be separable from a decision.

    Without the reason an automatic supersession is indistinguishable from a human
    one: the fact stops being recalled and nothing records the grounds.
    """
    coordinator = _coordinator(tmp_path)
    try:
        old = coordinator.ingest_candidate_fact(
            fact="the cache layer is disabled in production", scope="project",
        )
        coordinator.ingest_candidate_fact(
            fact="the cache layer is enabled in production", scope="project",
        )
        declared = coordinator.ingest_candidate_fact(
            fact="DECISION: ADR-910 we deploy with swarm", scope="project",
        )
        coordinator.ingest_candidate_fact(
            fact="DECISION: ADR-911 deploy tooling changed", scope="project",
            metadata={"supersedes": [declared.id]},
        )

        edges = {edge.superseded_id: edge for edge in coordinator.canonical_store.supersession_edges()}
        assert edges[old.id].reason == "polarity"
        assert edges[declared.id].reason == "declared"

        # The audit view is for the guesses; a declared supersession is not a guess.
        audited = {edge.superseded_id for edge in coordinator.auto_supersessions()}
        assert old.id in audited
        assert declared.id not in audited
    finally:
        coordinator.close()


def test_restore_returns_a_wrongly_superseded_fact_to_the_active_set(tmp_path: Path) -> None:
    """Undo a guess: the fact becomes recallable again and the lineage is cleaned up."""
    coordinator = _coordinator(tmp_path)
    try:
        old = coordinator.ingest_candidate_fact(
            fact="the cache layer is disabled in production", scope="project",
        )
        new = coordinator.ingest_candidate_fact(
            fact="the cache layer is enabled in production", scope="project",
        )
        assert old.id not in _active_ids(coordinator)

        outcome = coordinator.restore_fact(old.id)
        assert outcome.restored is True
        assert outcome.reason == "restored"

        active = _active_ids(coordinator)
        assert old.id in active, "the restored fact is still not active"
        assert new.id in active, "restoring one fact must not disturb the other"

        lineage = {
            fact.id: fact
            for fact in coordinator.list_facts(scope="project", include_superseded=True)
        }
        assert lineage[new.id].supersedes == []
        assert lineage[old.id].superseded_by is None
        assert coordinator.auto_supersessions() == []

        # Recall is derived from the journal, so the restored fact is retrievable again.
        hits = coordinator.canonical_store.search_fts("cache layer disabled", ["project"])
        assert old.id in [hit.record_id for hit in hits]
    finally:
        coordinator.close()


def test_restore_reports_a_conflict_instead_of_raising(tmp_path: Path) -> None:
    """``idx_memory_dedupe`` is UNIQUE per active content, so reviving a fact whose
    content is active again would collide. That has to be a reported outcome, not an
    opaque IntegrityError, and it must leave the journal untouched."""
    coordinator = _coordinator(tmp_path)
    try:
        old = coordinator.ingest_candidate_fact(
            fact="the cache layer is disabled in production", scope="project",
        )
        coordinator.ingest_candidate_fact(
            fact="the cache layer is enabled in production", scope="project",
        )
        # The same content comes back as its own active record.
        replacement = coordinator.ingest_candidate_fact(
            fact="the cache layer is disabled in production", scope="project",
        )
        assert replacement.id != old.id

        outcome = coordinator.restore_fact(old.id)
        assert outcome.restored is False
        assert outcome.reason == "active_duplicate_exists"
        assert old.id not in _active_ids(coordinator)

        # Nothing was half-applied.
        assert coordinator.canonical_store.restore("missing-record").reason == "not_found"
        assert coordinator.canonical_store.restore(replacement.id).reason == "not_superseded"
    finally:
        coordinator.close()
