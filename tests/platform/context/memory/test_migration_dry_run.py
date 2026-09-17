"""Migration dry run over a vault *copy*, with the real vault left untouched.

The contract this pins: a dry run is a complete, reviewable plan that writes
nothing — not to the canonical journal, not to any projection — and the numbers
it reports match what a real import then does. GraphRAG is rebuilt only by
replaying the canonical outbox, and scope/ownership travel with every record so
the imported facts are re-authorizable after the migration.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from hermes.platform.context.memory.access import MemoryAccessContext
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.embedding import HashingEmbedder
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.context.memory.graphrag_store import GraphRAGStore
from hermes.platform.context.memory.migration import MemoryMigrator
from hermes.platform.context.memory.obsidian import ObsidianAdapter
from hermes.platform.context.memory.projection_runner import ProjectionRunner


def _build_vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / "20-Architecture").mkdir(parents=True)
    (vault / "10-Notes").mkdir(parents=True)
    (vault / "20-Architecture" / "ADR-101-docker-swarm.md").write_text(
        "---\ntitle: ADR-101 Docker Swarm\n---\nADR-101: Deployments use Docker Swarm exclusively.\n",
        encoding="utf-8",
    )
    (vault / "20-Architecture" / "ADR-102-kubernetes.md").write_text(
        "---\ntitle: ADR-102 Kubernetes\n---\nADR-102: Deployments migrate to Kubernetes instead of Docker Swarm.\n",
        encoding="utf-8",
    )
    (vault / "10-Notes" / "postgres.md").write_text(
        "Postgres is the canonical store for auth data.\n", encoding="utf-8",
    )
    # Two files with identical content: the canonical store collapses these, and
    # the plan must predict that instead of double counting.
    (vault / "10-Notes" / "duplicate-a.md").write_text(
        "Retention window is 30 days.\n", encoding="utf-8",
    )
    (vault / "10-Notes" / "duplicate-b.md").write_text(
        "Retention window is 30 days.\n", encoding="utf-8",
    )
    return vault


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.fixture()
def workspace(tmp_path: Path):
    """A real vault plus a copy the migration is allowed to touch."""
    real_vault = _build_vault(tmp_path)
    working_copy = tmp_path / "vault-copy"
    shutil.copytree(real_vault, working_copy)
    return real_vault, working_copy, tmp_path


def _migrator(tmp_path: Path, vault: Path):
    coordinator = FederatedMemoryCoordinator(
        vault_path=vault,
        graphrag_store=GraphRAGStore(tmp_path / "graphrag.db"),
    )
    migrator = MemoryMigrator(coordinator.canonical_store, coordinator.projection_runner)
    return coordinator, migrator


def test_dry_run_plans_without_writing_and_preserves_uris_and_hashes(workspace):
    real_vault, working_copy, tmp_path = workspace
    before = _tree_digest(real_vault)
    coordinator, migrator = _migrator(tmp_path, working_copy)
    try:
        report = migrator.backfill_obsidian(working_copy, dry_run=True)

        assert report.dry_run is True
        assert report.notes_seen == 5
        assert report.decisions == 2
        assert report.imported == 0
        assert report.projections_replayed == 0

        # Nothing was written anywhere.
        assert coordinator.canonical_store.list_records() == []
        assert coordinator.canonical_store.pending_projections() == 0
        assert not (tmp_path / "vault-copy" / "20-Architecture").joinpath("x.md").exists()

        # URIs and hashes are preserved, and match the files on disk.
        assert set(report.uris) == {
            "obsidian://20-Architecture/ADR-101-docker-swarm.md",
            "obsidian://20-Architecture/ADR-102-kubernetes.md",
            "obsidian://10-Notes/postgres.md",
            "obsidian://10-Notes/duplicate-a.md",
            "obsidian://10-Notes/duplicate-b.md",
        }
        for entry in report.entries:
            source = working_copy / entry.relative_path
            assert entry.content_hash == migrator.content_hash(source.read_text(encoding="utf-8"))
            assert entry.record_id.startswith("legacy:")

        # Exactly the expected duplicate, and no others.
        assert len(report.duplicates) == 1
        assert report.duplicates[0].relative_path == "10-Notes/duplicate-b.md"
        assert report.duplicates[0].duplicate_of == "obsidian://10-Notes/duplicate-a.md"

        # Scopes and ownership are resolved up front.
        assert report.scopes == {"project": 5}
        assert report.owners == {"": 5}
    finally:
        coordinator.close()

    # The real vault was never touched.
    assert _tree_digest(real_vault) == before


def test_dry_run_owner_comes_from_the_principal(workspace):
    _real_vault, working_copy, tmp_path = workspace
    coordinator, migrator = _migrator(tmp_path, working_copy)
    try:
        access = MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))
        report = migrator.backfill_obsidian(working_copy, dry_run=True, access=access)
        assert report.owners == {"alice": 5}
        assert {entry.owner_id for entry in report.entries} == {"alice"}
    finally:
        coordinator.close()


def test_private_scope_import_persists_owner(tmp_path: Path):
    """Ownership is what makes a private import re-authorizable after restart."""
    vault = _build_vault(tmp_path)
    coordinator = FederatedMemoryCoordinator(vault_path=vault)
    migrator = MemoryMigrator(coordinator.canonical_store, coordinator.projection_runner)
    access = MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))
    try:
        report = migrator.backfill_obsidian(vault, default_scope="private", access=access, rebuild=False)
        assert report.imported == 4
        for record in coordinator.canonical_store.list_records(["private"]):
            assert record.metadata["owner_id"] == "alice"
        assert len(coordinator.list_facts(scope="private", access=access)) == 4
        bob = MemoryAccessContext("bob", frozenset({"team-b"}), frozenset({"project-b"}))
        assert coordinator.list_facts(scope="private", access=bob) == []
    finally:
        coordinator.close()


def test_real_import_matches_the_plan_and_rebuilds_graph_only_by_replay(workspace):
    _real_vault, working_copy, tmp_path = workspace
    access = MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))
    graph_path = tmp_path / "graphrag.db"
    coordinator = FederatedMemoryCoordinator(
        vault_path=working_copy, graphrag_store=GraphRAGStore(graph_path)
    )
    migrator = MemoryMigrator(coordinator.canonical_store, coordinator.projection_runner)
    try:
        plan = migrator.backfill_obsidian(working_copy, dry_run=True, access=access)
        graph_before = coordinator.graphrag_store.list_entities()
        assert graph_before == [], "GraphRAG must be empty before any replay"

        report = migrator.backfill_obsidian(working_copy, access=access)

        # The applied run matches the plan: 5 notes, 1 collapsed duplicate.
        assert report.imported == 4
        assert report.skipped_existing == 1
        assert report.notes_seen == plan.notes_seen == 5
        assert len(report.duplicates) == len(plan.duplicates) == 1

        records = coordinator.canonical_store.list_records()
        assert len(records) == 4

        # Hashes and URIs survived into the canonical journal.
        by_uri = {}
        for record in records:
            assert len(record.provenance) == 1
            uri = record.provenance[0]["uri"]
            by_uri[uri] = record
            assert record.metadata["legacy_uri"] == uri
            source = working_copy / uri.removeprefix("obsidian://")
            assert record.content_hash == migrator.content_hash(source.read_text(encoding="utf-8"))
        assert set(by_uri) == {
            "obsidian://20-Architecture/ADR-101-docker-swarm.md",
            "obsidian://20-Architecture/ADR-102-kubernetes.md",
            "obsidian://10-Notes/postgres.md",
            "obsidian://10-Notes/duplicate-a.md",
        }

        # Scope and tenancy are persisted, so the imported facts stay readable
        # only to the principal that owns them.
        for record in records:
            assert record.scope == "project"
            assert record.metadata["project_ids"] == ["project-a"]
        assert {entry.owner_id for entry in plan.entries} == {"alice"}
        assert len(coordinator.list_facts(scope="project", access=access)) == 4
        other = MemoryAccessContext("bob", frozenset({"team-b"}), frozenset({"project-b"}))
        assert coordinator.list_facts(scope="project", access=other) == []

        # GraphRAG exists only because the canonical outbox was replayed.
        assert report.projections_replayed >= 4
        assert coordinator.graphrag_store.list_entities(), "replay did not populate GraphRAG"
        assert coordinator.canonical_store.pending_projections() == 0

        # Idempotent: a second import adds nothing.
        again = migrator.backfill_obsidian(working_copy, access=access)
        assert again.imported == 0
        assert len(coordinator.canonical_store.list_records()) == 4
    finally:
        coordinator.close()
