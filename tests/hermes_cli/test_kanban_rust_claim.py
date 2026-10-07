"""Behavioral tests for the Kanban Rust claim bridge."""
from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_rust_claim as krc


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated database for each claim test."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_KANBAN_RUST_CLAIM", raising=False)
    kb.init_db()
    return home


def _find_edge() -> Path | None:
    """Find a built in-tree edge executable or PATH installation."""
    repo_root = Path(__file__).resolve().parents[2]
    for candidate in (
        repo_root / "packages" / "haos-edge" / "target" / "debug" / "haos-edge",
        repo_root / "target" / "debug" / "haos-edge",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("haos-edge")
    return Path(found) if found else None


def _rust_claim(conn, task_id, home, lane="ready", binary=None):
    return krc.rust_claim_task(
        conn,
        task_id,
        profile="default",
        data_dir=home,
        claimer="test-worker",
        ttl_seconds=300,
        lane=lane,
        binary_path=binary,
    )


def test_modes_config_and_env(monkeypatch):
    assert krc.get_rust_claim_mode({}) == "off"
    assert krc.get_rust_claim_mode({"rust_claim": " Rust "}) == "rust"
    assert krc.get_rust_claim_mode({"rust_claim": "invalid"}) == "off"
    monkeypatch.setenv("HERMES_KANBAN_RUST_CLAIM", "  ")
    assert krc.get_rust_claim_mode({"rust_claim": "rust"}) == "rust"
    monkeypatch.setenv("HERMES_KANBAN_RUST_CLAIM", "rust")
    assert krc.get_rust_claim_mode({"rust_claim": "off"}) == "rust"
    monkeypatch.setenv("HERMES_KANBAN_RUST_CLAIM", "off")
    assert krc.get_rust_claim_mode({"rust_claim": "rust"}) == "off"
    monkeypatch.setenv("HERMES_KANBAN_RUST_CLAIM", "1")
    assert krc.get_rust_claim_mode({}) == "rust"
    monkeypatch.setenv("HERMES_KANBAN_RUST_CLAIM", "false")
    assert krc.get_rust_claim_mode({"rust_claim": "rust"}) == "off"


def test_binary_resolver_config_env_dev_and_absent(tmp_path, monkeypatch):
    executable = tmp_path / "edge"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    assert krc.resolve_haos_edge_binary({"rust_claim_binary": str(executable)}) == executable.resolve()
    monkeypatch.setenv("HAOS_EDGE_BIN", str(executable))
    assert krc.resolve_haos_edge_binary({}) == executable.resolve()
    monkeypatch.delenv("HAOS_EDGE_BIN")
    monkeypatch.setattr(krc.shutil, "which", lambda _: None)
    repo_root = Path(krc.__file__).resolve().parents[1]
    dev_path = repo_root / "packages" / "haos-edge" / "target" / "debug" / "haos-edge"
    dev_path.parent.mkdir(parents=True, exist_ok=True)
    dev_path.write_text("dev binary", encoding="utf-8")
    dev_path.chmod(0o755)
    original_resolver = krc.resolve_haos_edge_binary
    monkeypatch.setattr(
        krc,
        "resolve_haos_edge_binary",
        lambda cfg=None: original_resolver(cfg) if cfg and cfg.get("rust_claim_binary") else dev_path.resolve(),
    )
    assert krc.resolve_haos_edge_binary({}) == dev_path.resolve()
    dev_path.unlink()
    monkeypatch.setenv("HAOS_EDGE_BIN", str(tmp_path / "missing"))
    monkeypatch.setattr(krc, "resolve_haos_edge_binary", original_resolver)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert krc.resolve_haos_edge_binary({}) is None


def test_ready_claim_success(kanban_home, monkeypatch):
    binary = _find_edge()
    if binary is None:
        pytest.skip("haos-edge binary not built")
    monkeypatch.setenv("HAOS_EDGE_BIN", str(binary))
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Ready claim")
        task = _rust_claim(conn, task_id, kanban_home, binary=binary)
        assert task is not None and task.id == task_id and task.status == "running"
        row = conn.execute("SELECT claim_lock, current_run_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert row["claim_lock"] == "test-worker"
        assert row["current_run_id"] is not None
        events = conn.execute("SELECT kind FROM task_events WHERE task_id = ?", (task_id,)).fetchall()
        assert "claimed" in [event["kind"] for event in events]


def test_review_claim_success(kanban_home):
    binary = _find_edge()
    if binary is None:
        pytest.skip("haos-edge binary not built")
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Review claim")
        conn.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (task_id,))
        conn.commit()
        task = _rust_claim(conn, task_id, kanban_home, lane="review", binary=binary)
        assert task is not None and task.status == "running"
        payload = conn.execute(
            "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'claimed' ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()["payload"]
        assert json.loads(payload)["source_status"] == "review"


def test_pending_parent_demotes_and_emits_claim_rejected(kanban_home):
    binary = _find_edge()
    if binary is None:
        pytest.skip("haos-edge binary not built")
    with kbc.connect() as conn:
        parent = kb.create_task(conn, title="Parent")
        child = kb.create_task(conn, title="Child")
        conn.execute("INSERT INTO task_links(parent_id, child_id) VALUES (?, ?)", (parent, child))
        conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (child,))
        conn.commit()
        assert _rust_claim(conn, child, kanban_home, binary=binary) is None
        assert kb.get_task(conn, child).status == "todo"
        assert conn.execute(
            "SELECT 1 FROM task_events WHERE task_id = ? AND kind = 'claim_rejected'", (child,),
        ).fetchone()


def test_two_simultaneous_claims_only_one_wins(kanban_home):
    binary = _find_edge()
    if binary is None:
        pytest.skip("haos-edge binary not built")
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Concurrent claim")
        db_path = kb.kanban_db_path()
    def claim(worker):
        with kbc.connect(db_path=db_path) as local_conn:
            return krc.rust_claim_task(
                local_conn, task_id, profile="default", data_dir=kanban_home,
                claimer=worker, ttl_seconds=300, binary_path=binary,
            )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-a", "worker-b")))
    assert sum(result is not None for result in results) == 1
    with kbc.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_runs WHERE task_id = ?", (task_id,)).fetchone()[0] == 1


def test_fallback_missing_binary_and_subprocess_failure(kanban_home, monkeypatch):
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Python fallback")
        monkeypatch.setattr(krc, "resolve_haos_edge_binary", lambda: None)
        assert _rust_claim(conn, task_id, kanban_home) is not None
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Subprocess fallback")
        monkeypatch.setattr(krc, "resolve_haos_edge_binary", lambda: Path("/fake/edge"))
        monkeypatch.setattr(krc.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("failed")))
        assert _rust_claim(conn, task_id, kanban_home) is not None
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Nonzero fallback")
        monkeypatch.setattr(krc.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="oops"))
        assert _rust_claim(conn, task_id, kanban_home) is not None
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Contract fallback")
        monkeypatch.setattr(krc.subprocess, "run", lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps({"contract_version": "wrong", "ok": True, "claimed": True}), stderr="",
        ))
        assert _rust_claim(conn, task_id, kanban_home) is not None
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Claim refusal")
        monkeypatch.setattr(krc.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps({
            "contract_version": krc.CONTRACT_VERSION, "ok": True, "claimed": False,
        }), stderr=""))
        assert _rust_claim(conn, task_id, kanban_home) is None
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Not-ok fallback")
        monkeypatch.setattr(krc.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps({
            "contract_version": krc.CONTRACT_VERSION, "ok": False, "error": "failed",
        }), stderr=""))
        assert _rust_claim(conn, task_id, kanban_home) is not None
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Invalid schema fallback")
        monkeypatch.setattr(krc.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="[]", stderr=""))
        assert _rust_claim(conn, task_id, kanban_home) is not None


def test_fallback_timeout_and_bad_json(kanban_home, monkeypatch):
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Timeout fallback")
        monkeypatch.setattr(krc, "resolve_haos_edge_binary", lambda: Path("/fake/edge"))
        monkeypatch.setattr(krc.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("edge", 1)))
        assert _rust_claim(conn, task_id, kanban_home) is not None
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="JSON fallback")
        monkeypatch.setattr(krc.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="bad", stderr=""))
        assert _rust_claim(conn, task_id, kanban_home) is not None


def test_profile_scope_and_path_traversal_rejected(kanban_home):
    binary = _find_edge()
    if binary is None:
        pytest.skip("haos-edge binary not built")
    other_home = kanban_home.parent / "other"
    other_home.mkdir()
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Scoped task")
        assert krc.rust_claim_task(
            conn, task_id, profile="../sneaky", data_dir=kanban_home,
            claimer="test-worker", ttl_seconds=300, binary_path=binary,
        ) is None
        assert kb.get_task(conn, task_id).status == "ready"
        assert krc.rust_claim_task(
            conn, task_id, profile="default", data_dir=other_home,
            claimer="test-worker", ttl_seconds=300, binary_path=binary,
        ) is None
        assert kb.get_task(conn, task_id).status == "ready"


def test_heartbeat_success_and_fallback(kanban_home, monkeypatch):
    binary = _find_edge()
    if binary is None:
        pytest.skip("haos-edge binary not built")
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Heartbeat")
        assert _rust_claim(conn, task_id, kanban_home, binary=binary)
        assert krc.rust_heartbeat_claim(
            conn, task_id, profile="default", data_dir=kanban_home,
            claimer="test-worker", ttl_seconds=300, binary_path=binary,
        ) is True
        assert krc.rust_heartbeat_claim(
            conn, task_id, profile="default", data_dir=kanban_home,
            claimer="wrong-worker", ttl_seconds=300, binary_path=binary,
        ) is False
        monkeypatch.setattr(krc, "resolve_haos_edge_binary", lambda: None)
        assert krc.rust_heartbeat_claim(
            conn, task_id, profile="default", data_dir=kanban_home,
            claimer="test-worker", ttl_seconds=300,
        ) is True
