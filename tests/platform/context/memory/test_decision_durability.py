"""The ``decisions`` projection must be durable, not a cache.

It used to be a plain in-memory dict, so every restart silently emptied it while
``recover_projections()`` had nothing to replay — the outbox job was already
acknowledged. The canonical journal kept the truth, but a projection that vanishes on
restart is a cache, and the legacy reader (shadow mode / rollback) reads from here.
"""

from __future__ import annotations

from pathlib import Path

from hermes.platform.context.memory.decisions import DecisionStore
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator


def _coordinator(tmp_path: Path) -> FederatedMemoryCoordinator:
    return FederatedMemoryCoordinator(vault_path=tmp_path / "vault")


def test_decisions_survive_restart_with_supersession(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        old = coordinator.ingest_candidate_fact(
            fact="DECISION: ADR-900 the decisions projection must survive restart",
            scope="project",
        )
        new = coordinator.ingest_candidate_fact(
            fact="DECISION: ADR-901 it must also carry supersession",
            scope="project",
            metadata={"supersedes": [old.id]},
        )
        assert coordinator.decisions.count() == 2
    finally:
        coordinator.close()

    reopened = _coordinator(tmp_path)
    try:
        assert reopened.decisions.count() == 2, "the projection was lost on restart"
        assert reopened.decisions.get_decision(new.id) is not None

        superseded = reopened.decisions.get_decision(old.id)
        assert superseded.status == "superseded"
        assert superseded.superseded_by == new.id

        # Only the surviving decision is retrievable, and it is found by content.
        assert [item.source_uri for item in reopened.decisions.retrieve()] == [f"decision://{new.id}"]
        assert [item.source_uri for item in reopened.decisions.retrieve(query="ADR-901")] == [
            f"decision://{new.id}"
        ]
    finally:
        reopened.close()


def test_decisions_replay_is_idempotent(tmp_path: Path) -> None:
    """Re-applying the projection from the outbox must not duplicate or corrupt history."""
    coordinator = _coordinator(tmp_path)
    try:
        fact = coordinator.ingest_candidate_fact(
            fact="DECISION: ADR-902 replaying the projection must be idempotent",
            scope="project",
        )
        before = coordinator.decisions.count()
        coordinator.decisions.record_decision(
            fact.id, f"Decision - {fact.id}", "DECISION: ADR-902 replayed verbatim",
        )
        assert coordinator.decisions.count() == before
        assert coordinator.decisions.get_decision(fact.id) is not None
    finally:
        coordinator.close()


def test_decisions_mapping_view_reads_through_to_storage(tmp_path: Path) -> None:
    """``_decisions`` stays a working mapping for existing readers, and is never a second
    source of truth: it reflects what was persisted, including after a restart."""
    store = DecisionStore(tmp_path / "decisions.db")
    try:
        store.record_decision("ADR-100", "First", "content one")
        assert "ADR-100" in store._decisions
        assert store._decisions["ADR-100"].title == "First"
        assert len(store._decisions) == 1
        assert list(store._decisions) == ["ADR-100"]
    finally:
        store.close()

    reopened = DecisionStore(tmp_path / "decisions.db")
    try:
        assert "ADR-100" in reopened._decisions, "the view lost persisted state"
        assert reopened._decisions["ADR-100"].title == "First"
    finally:
        reopened.close()


def test_in_memory_store_stays_disposable(tmp_path: Path) -> None:
    """``DecisionStore()`` with no path keeps working as a throwaway store."""
    store = DecisionStore()
    try:
        store.record_decision("ADR-200", "Ephemeral", "content")
        assert store.count() == 1
        assert store.get_decision("ADR-200") is not None
    finally:
        store.close()
