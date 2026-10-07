"""Tests for Kanban Rust Candidate Selector Bridge (Read-Only Opt-in).

Verifies:
1. Rust haos-edge integration with temporary SQLite DB and profile isolation.
2. Modes: 'off' (default), 'shadow', 'rust'.
3. Candidate order matching between Rust selector and Python.
4. Python reclaim/lock/claim/spawn integrity preserved when Rust selector is active.
5. Error handling and failover: graceful fallback to Python if Rust binary is missing or fails.
6. Stale/locked tasks correctly ignored by Rust selector.
7. A -> B -> A profile and database isolation.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_rust_selector as krs


@pytest.fixture
def kanban_env(tmp_path, monkeypatch):
    """Isolated HERMES_HOME and temporary Kanban database."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _find_target_haos_edge() -> str | None:
    # Resolve relative to the repository / worktree root or PATH
    repo_root = Path(__file__).resolve().parents[2]
    possible_paths = [
        repo_root / "target" / "debug" / "haos-edge",
        repo_root / "packages" / "haos-edge" / "target" / "debug" / "haos-edge",
    ]
    for p in possible_paths:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which("haos-edge")


def test_rust_selector_unit_contract_and_ordering(kanban_env, monkeypatch):
    bin_path = _find_target_haos_edge()
    if not bin_path:
        pytest.skip("haos-edge binary not found")
    monkeypatch.setenv("HAOS_EDGE_BIN", bin_path)

    db_path = kb.kanban_db_path()
    with kbc.connect() as conn:
        # Create tasks in specific priority and created_at order
        t1 = kb.create_task(conn, title="Low priority", priority=0)
        t2 = kb.create_task(conn, title="High priority", priority=10)
        t3 = kb.create_task(conn, title="Medium priority", priority=5)

    candidates = krs.query_rust_candidates(
        db_path,
        profile="default",
        data_dir=kanban_env,
        include_review=False,
    )
    assert candidates is not None
    assert len(candidates) == 3
    # Rust selector orders by priority DESC, created_at ASC, id ASC
    ids = [c["id"] for c in candidates]
    assert ids == [t2, t3, t1]


def test_rust_selector_ignores_claimed_and_other_statuses(kanban_env, monkeypatch):
    bin_path = _find_target_haos_edge()
    if not bin_path:
        pytest.skip("haos-edge binary not found")
    monkeypatch.setenv("HAOS_EDGE_BIN", bin_path)

    db_path = kb.kanban_db_path()
    with kbc.connect() as conn:
        t_ready = kb.create_task(conn, title="Ready task")
        t_locked = kb.create_task(conn, title="Locked task")
        conn.execute("UPDATE tasks SET claim_lock = 'worker-1' WHERE id = ?", (t_locked,))
        conn.commit()

    candidates = krs.query_rust_candidates(
        db_path,
        profile="default",
        data_dir=kanban_env,
        include_review=False,
    )
    assert candidates is not None
    ids = [c["id"] for c in candidates]
    assert t_ready in ids
    assert t_locked not in ids


def test_rust_selector_modes_off_shadow_rust(kanban_env, monkeypatch):
    bin_path = _find_target_haos_edge()
    if not bin_path:
        pytest.skip("haos-edge binary not found")
    monkeypatch.setenv("HAOS_EDGE_BIN", bin_path)

    # 1. Mode: off (default)
    monkeypatch.delenv("HERMES_KANBAN_RUST_SELECTOR", raising=False)
    assert krs.get_rust_selector_mode() == "off"

    # 2. Mode: shadow
    monkeypatch.setenv("HERMES_KANBAN_RUST_SELECTOR", "shadow")
    assert krs.get_rust_selector_mode() == "shadow"

    # 3. Mode: rust
    monkeypatch.setenv("HERMES_KANBAN_RUST_SELECTOR", "rust")
    assert krs.get_rust_selector_mode() == "rust"


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_dispatch_once_integration_with_rust_selector(kanban_home, all_assignees_spawnable, monkeypatch):
    bin_path = _find_target_haos_edge()
    if not bin_path:
        pytest.skip("haos-edge binary not found")
    monkeypatch.setenv("HAOS_EDGE_BIN", bin_path)
    monkeypatch.setenv("HERMES_KANBAN_RUST_SELECTOR", "rust")

    conn = kbc.connect()
    try:
        t1 = kb.create_task(conn, title="Task 1", priority=1, assignee="alice")
        t2 = kb.create_task(conn, title="Task 2", priority=10, assignee="alice")

        def fake_spawn(*args, **kwargs):
            return 9999

        # Run dispatch_once with Rust selector active
        res = kbd.dispatch_once(conn, dry_run=False, spawn_fn=fake_spawn)

        # High priority task should be spawned first
        spawned_ids = [row[0] for row in res.spawned]
        assert spawned_ids == [t2, t1], f"res.spawned={res.spawned}, nonspawnable={res.skipped_nonspawnable}, unassigned={res.skipped_unassigned}"

        # Verify claim locks set in SQLite
        row2 = kb.get_task(conn, t2)
        assert row2 is not None
        assert row2.status == "running"
        assert row2.claim_lock is not None
    finally:
        conn.close()


def test_rust_selector_fallback_on_failure(kanban_home, all_assignees_spawnable, monkeypatch):
    # Point to nonexistent binary
    monkeypatch.setenv("HAOS_EDGE_BIN", "/nonexistent/haos-edge")
    monkeypatch.setenv("HERMES_KANBAN_RUST_SELECTOR", "rust")

    conn = kbc.connect()
    try:
        t1 = kb.create_task(conn, title="Fallback task", assignee="alice")

        def fake_spawn(*args, **kwargs):
            return 8888

        # Should log warning and fall back to Python selector smoothly without exception
        res = kbd.dispatch_once(conn, dry_run=False, spawn_fn=fake_spawn)
        spawned_ids = [row[0] for row in res.spawned]
        assert spawned_ids == [t1], f"res.spawned={res.spawned}, nonspawnable={res.skipped_nonspawnable}, unassigned={res.skipped_unassigned}"
    finally:
        conn.close()


def test_profile_isolation_a_b_a(tmp_path, monkeypatch):
    bin_path = _find_target_haos_edge()
    if not bin_path:
        pytest.skip("haos-edge binary not found")
    monkeypatch.setenv("HAOS_EDGE_BIN", bin_path)

    # Home A
    home_a = tmp_path / "home_a"
    home_a.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home_a))
    monkeypatch.setattr(Path, "home", lambda: home_a)
    kb.init_db()
    with kbc.connect() as conn_a:
        t_a = kb.create_task(conn_a, title="Task in Home A")
    db_a = kb.kanban_db_path()

    # Home B
    home_b = tmp_path / "home_b"
    home_b.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home_b))
    monkeypatch.setattr(Path, "home", lambda: home_b)
    kb.init_db()
    with kbc.connect() as conn_b:
        t_b = kb.create_task(conn_b, title="Task in Home B")
    db_b = kb.kanban_db_path()

    # Query Home A
    cands_a = krs.query_rust_candidates(db_a, profile="default", data_dir=home_a)
    assert cands_a is not None
    assert [c["id"] for c in cands_a] == [t_a]

    # Query Home B
    cands_b = krs.query_rust_candidates(db_b, profile="default", data_dir=home_b)
    assert cands_b is not None
    assert [c["id"] for c in cands_b] == [t_b]

    # Return to Home A
    cands_a2 = krs.query_rust_candidates(db_a, profile="default", data_dir=home_a)
    assert cands_a2 is not None
    assert [c["id"] for c in cands_a2] == [t_a]


def test_malformed_scope_and_path_traversal_rejected(tmp_path, monkeypatch):
    bin_path = _find_target_haos_edge()
    if not bin_path:
        pytest.skip("haos-edge binary not found")
    monkeypatch.setenv("HAOS_EDGE_BIN", bin_path)

    home = tmp_path / "home_valid"
    home.mkdir()
    other_home = tmp_path / "home_other"
    other_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    kb.init_db()
    with kbc.connect() as conn:
        t = kb.create_task(conn, title="Valid task")
    valid_db = kb.kanban_db_path()

    # DB outside the bound data_dir must be rejected with scope error
    cands_violation = krs.query_rust_candidates(valid_db, profile="default", data_dir=other_home)
    assert cands_violation is None

    # Invalid profile with slashes or relative components
    cands_invalid_prof = krs.query_rust_candidates(valid_db, profile="../sneaky", data_dir=home)
    assert cands_invalid_prof is None


def test_stale_candidate_revalidated_by_python_claims(kanban_home, all_assignees_spawnable, monkeypatch):
    bin_path = _find_target_haos_edge()
    if not bin_path:
        pytest.skip("haos-edge binary not found")
    monkeypatch.setenv("HAOS_EDGE_BIN", bin_path)
    monkeypatch.setenv("HERMES_KANBAN_RUST_SELECTOR", "rust")

    conn = kbc.connect()
    try:
        t1 = kb.create_task(conn, title="Task Stale", priority=10, assignee="alice")
        t2 = kb.create_task(conn, title="Task Fresh", priority=5, assignee="alice")

        # Simulate race: right before Python reads the candidate row in dispatcher, t1 gets claimed or status altered
        def mock_query(*args, **kwargs):
            # Alter t1 status directly in SQLite to simulate stale Rust candidate
            conn.execute("UPDATE tasks SET status = 'running', claim_lock = 'other_worker' WHERE id = ?", (t1,))
            conn.commit()
            return [
                {"id": t1, "assignee": "alice", "status": "ready", "lane": "ready", "priority": 10, "created_at": 100},
                {"id": t2, "assignee": "alice", "status": "ready", "lane": "ready", "priority": 5, "created_at": 200},
            ]

        monkeypatch.setattr(krs, "query_rust_candidates", mock_query)

        spawns = []
        def fake_spawn(task, *args, **kwargs):
            spawns.append(task.id)
            return 999

        res = kbd.dispatch_once(conn, dry_run=False, spawn_fn=fake_spawn)
        # Only t2 should be spawned; t1 was stale and skipped safely without error
        assert spawns == [t2]
        assert [r[0] for r in res.spawned] == [t2]
    finally:
        conn.close()


def test_timeout_falls_back_gracefully_to_python(kanban_home, all_assignees_spawnable, monkeypatch):
    conn = kbc.connect()
    try:
        t1 = kb.create_task(conn, title="Timeout task", assignee="alice")

        # Force timeout in query_rust_candidates
        def mock_timeout(*args, **kwargs):
            return None

        monkeypatch.setenv("HERMES_KANBAN_RUST_SELECTOR", "rust")
        monkeypatch.setattr(krs, "query_rust_candidates", mock_timeout)

        spawns = []
        def fake_spawn(task, *args, **kwargs):
            spawns.append(task.id)
            return 888

        res = kbd.dispatch_once(conn, dry_run=False, spawn_fn=fake_spawn)
        # Gracefully falls back to Python selector and spawns t1
        assert spawns == [t1]
        assert [r[0] for r in res.spawned] == [t1]
    finally:
        conn.close()


def test_ordering_parity_between_python_and_rust(kanban_home, monkeypatch):
    bin_path = _find_target_haos_edge()
    if not bin_path:
        pytest.skip("haos-edge binary not found")
    monkeypatch.setenv("HAOS_EDGE_BIN", bin_path)

    conn = kbc.connect()
    try:
        # Create multiple tasks with matching and differing priorities and timestamps
        t1 = kb.create_task(conn, title="P10-older", priority=10)
        t2 = kb.create_task(conn, title="P10-newer", priority=10)
        t3 = kb.create_task(conn, title="P20", priority=20)
        t4 = kb.create_task(conn, title="P0", priority=0)

        db_path = kb.kanban_db_path()
        rust_cands = krs.query_rust_candidates(db_path, profile="default", data_dir=kanban_home)
        assert rust_cands is not None
        rust_ids = [c["id"] for c in rust_cands]

        # Python canonical lane rows
        py_rows = kbd._lane_rows(conn, "ready")
        py_ids = [r["id"] for r in py_rows]

        assert rust_ids == py_ids
    finally:
        conn.close()
