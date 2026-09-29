import pytest
from pathlib import Path
from hermes.platform.bots.identity import (
    BotIdentityBundle,
    IdentityVersion,
    LeafIdentitySnapshot,
)
from hermes.platform.bots.leaf_protocol import (
    build_temporary_soul,
    create_leaf_identity_snapshot,
)
from hermes.platform.observability.event_store import EventStore
from hermes.platform.shadow_leaf import ShadowLeaf, ShadowLeafManager


def test_build_temporary_soul():
    parent_soul = "Architect: Focus on clean, modular, maintainable systems."
    task = "Design SQLite schema for civilization events"
    constraints = ["No foreign keys that block WAL", "Must support append-only"]
    council = "Architecture Council #1"

    temp_soul = build_temporary_soul(
        parent_soul=parent_soul,
        task_description=task,
        constraints=constraints,
        council_context=council,
    )

    assert "Architect: Focus on clean" in temp_soul
    assert "## Mission Focus\nDesign SQLite schema" in temp_soul
    assert "## Mission Constraints\n- No foreign keys" in temp_soul
    assert "## Council Context\nArchitecture Council #1" in temp_soul


def test_create_leaf_identity_snapshot():
    bundle = BotIdentityBundle(bot_id="architect", soul="Core Soul", identity="Architect v1")
    ver = IdentityVersion(id="ver-10", bot_id="architect", version=10, bundle_hash=bundle.bundle_hash, bundle=bundle)

    snap = create_leaf_identity_snapshot(
        leaf_id="leaf-007",
        parent_bot_id="architect",
        identity_version=ver,
        task_description="Investigate memory leaks",
        constraints=["Max 10 minutes"],
        council_id="qa-council",
        correlation_id="corr-99",
    )

    assert snap.leaf_id == "leaf-007"
    assert snap.parent_bot_id == "architect"
    assert snap.identity_version_id == "ver-10"
    assert snap.bot_identity_version == 10
    assert snap.identity_bundle_hash == ver.bundle_hash
    assert snap.temporary_soul_hash != ""
    assert "Investigate memory leaks" in snap.temporary_soul
    assert snap.correlation_id == "corr-99"


def test_shadow_leaf_with_snapshot_and_events(tmp_path):
    import subprocess
    store = EventStore()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Tester"], check=True)
    (repo_dir / "README.md").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "init"], check=True, capture_output=True)

    shadows_dir = tmp_path / "shadows"

    mgr = ShadowLeafManager(base_repo_dir=repo_dir, shadows_root=shadows_dir, event_store=store)

    snap = LeafIdentitySnapshot(
        leaf_id="leaf-test",
        parent_bot_id="parent-bot",
        identity_version_id="ver-1",
        temporary_soul="Temp soul",
    )

    # Spawn shadow with fake worker to avoid git worktree dependency in unit test
    def mock_worker(leaf: ShadowLeaf):
        leaf.summary = "Execution success"

    leaf = mgr.spawn_shadow(
        parent_bot_id="parent-bot",
        parent_bot_name="Parent",
        profile="default",
        task_description="Task A",
        custom_leaf_id="leaf-test",
        executor_fn=mock_worker,
        run_in_background=False,
        identity_snapshot=snap,
    )

    assert leaf.identity_snapshot is not None
    assert leaf.identity_snapshot.leaf_id == "leaf-test"
    assert leaf.status == "completed"

    # Verify events
    events = store.get_all()
    event_names = [e.name for e in events]
    assert "civ.leaf.created" in event_names
    assert "civ.leaf.completed" in event_names

    # Verify registry serialization and loading
    mgr2 = ShadowLeafManager(base_repo_dir=repo_dir, shadows_root=shadows_dir)
    loaded = mgr2.get_shadow("leaf-test")
    assert loaded is not None
    assert loaded.identity_snapshot is not None
    assert loaded.identity_snapshot.temporary_soul == "Temp soul"
