"""End-to-end Memory Fabric integration: write, crash, recover, retrieve, deny.

The scenario is the one that decides whether the fabric is trustworthy, and it
is exercised against real files with a real process death — no mocked store:

1. a session writes a decision; the canonical journal commits it and enqueues
   four durable projection jobs;
2. the Obsidian projection is acknowledged, then the process is killed
   (``os._exit``) while the remaining projections are still leased;
3. a fresh coordinator opens the same journal, reclaims the abandoned lease and
   finishes the outstanding projections;
4. retrieval works over both channels — FTS and the vector index — and the
   Obsidian projection is *not* re-applied, because it was already acknowledged;
5. a principal from another tenant gets nothing back, at retrieval and at
   coordinator level.

Step 2 runs in a subprocess so the kill is a real process death rather than a
simulated exception: durability claims are only worth what a real crash proves.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from hermes.platform.context.memory.access import MemoryAccessContext
from hermes.platform.context.memory.embedding import HashingEmbedder
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.context.memory.retrieval import HybridMemoryRetriever

DECISION = "DECISION: the canonical journal is the only source of truth for memory."
TITLE = "ADR-900 canonical journal"

CRASH_CHILD = textwrap.dedent(
    """
    import os, sys

    from hermes.platform.context.memory.access import MemoryAccessContext
    from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator

    vault = sys.argv[1]
    alice = MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))
    coordinator = FederatedMemoryCoordinator(vault_path=vault, projection_lease_seconds=0.2)

    # Die the instant the *second* projection is attempted: by then Obsidian has
    # been acknowledged and committed, and the rest are still leased.
    coordinator.projection_runner.projectors["decisions"] = lambda record: os._exit(9)

    coordinator.ingest_candidate_fact(
        fact=%r,
        scope="project",
        provenance="session://crash",
        confidence=0.99,
        metadata={"title": %r},
        access_context=alice,
        sync=True,
    )
    os._exit(0)  # unreachable if the crash projector ran
    """
) % (DECISION, TITLE)


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "vault"
    path.mkdir()
    return path


def _alice() -> MemoryAccessContext:
    return MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))


def _bob() -> MemoryAccessContext:
    return MemoryAccessContext("bob", frozenset({"team-b"}), frozenset({"project-b"}))


def _crash_after_obsidian(vault: Path) -> None:
    script = vault.parent / "crash_writer.py"
    script.write_text(CRASH_CHILD, encoding="utf-8")
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, str(script), str(vault)],
        cwd=root, env=env, capture_output=True, text=True, timeout=120,
    )
    # Exit 9 is the crash projector; anything else means the scenario did not run.
    assert completed.returncode == 9, (
        "expected the writer to die mid-projection, got %s\nstdout=%s\nstderr=%s"
        % (completed.returncode, completed.stdout, completed.stderr)
    )


def test_write_crash_recover_retrieve_and_deny(vault: Path) -> None:
    _crash_after_obsidian(vault)

    # -- 3. restart on the same journal ------------------------------------
    coordinator = FederatedMemoryCoordinator(vault_path=vault, projection_lease_seconds=0.2)
    try:
        # The canonical record survived the kill.
        facts = coordinator.list_facts(scope="project", access=_alice())
        assert len(facts) == 1
        record = facts[0]
        assert record.fact == DECISION
        assert record.superseded_by is None

        # Obsidian was acknowledged before the crash, so its note is complete.
        note = vault / "20-Architecture" / ("%s.md" % record.id)
        assert note.exists(), "the acknowledged Obsidian projection is missing"
        assert DECISION in note.read_text(encoding="utf-8")

        # Obsidian must NOT be projected a second time during recovery.
        replayed: list[str] = []
        real_obsidian = coordinator.projection_runner.projectors["obsidian"]

        def counting_obsidian(stored):
            replayed.append(stored.record_id)
            return real_obsidian(stored)

        coordinator.projection_runner.projectors["obsidian"] = counting_obsidian

        # The killed process still held a lease on the remaining projections.
        assert coordinator.canonical_store.pending_projections() > 0

        deadline = time.monotonic() + 10.0
        while coordinator.canonical_store.pending_projections() and time.monotonic() < deadline:
            coordinator.recover_projections()
            time.sleep(0.02)
        assert coordinator.canonical_store.pending_projections() == 0, "recovery left jobs pending"
        assert coordinator.canonical_store.failed_projections() == 0
        assert replayed == [], "an already-acknowledged projection was replayed"

        # -- 4. retrieval over both channels -------------------------------
        fts_hits = coordinator.canonical_store.search_fts("canonical journal", ["project"])
        assert [hit.record_id for hit in fts_hits] == [record.id]

        assert coordinator.vector_index.count() == 1
        assert coordinator.pending_vector_reindex() == 0
        assert coordinator._vector_search("canonical journal", ["project"], 5) == [record.id]

        retriever = HybridMemoryRetriever(coordinator.canonical_store, coordinator._vector_search)
        hits = retriever.retrieve("canonical journal", ["project"], access=_alice())
        assert [hit.record.record_id for hit in hits] == [record.id]
        assert hits[0].channels == ("fts", "vector")
        context = retriever.format_context("canonical journal", ["project"], access=_alice())
        assert DECISION in context

        # -- 5. scope denial ----------------------------------------------
        assert retriever.retrieve("canonical journal", ["project"], access=_bob()) == []
        assert retriever.format_context("canonical journal", ["project"], access=_bob()) == ""
        assert coordinator.list_facts(scope="project", access=_bob()) == []
        assert coordinator.query("canonical journal", scope="project", access=_bob()) == []
    finally:
        coordinator.close()


def test_recovery_is_idempotent_across_restarts(vault: Path) -> None:
    """Reopening and recovering repeatedly must not duplicate any projection."""
    _crash_after_obsidian(vault)

    notes: set[str] = set()
    for _ in range(3):
        coordinator = FederatedMemoryCoordinator(vault_path=vault, projection_lease_seconds=0.2)
        try:
            deadline = time.monotonic() + 10.0
            while coordinator.canonical_store.pending_projections() and time.monotonic() < deadline:
                coordinator.recover_projections()
                time.sleep(0.02)
            assert coordinator.canonical_store.pending_projections() == 0
            assert len(coordinator.list_facts(scope="project")) == 1
            assert coordinator.vector_index.count() == 1
            notes = {str(path.relative_to(vault)) for path in vault.rglob("*.md")}
            assert len(notes) == 1
        finally:
            coordinator.close()
    assert len(notes) == 1
