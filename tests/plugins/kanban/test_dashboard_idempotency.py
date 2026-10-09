"""End-to-end idempotency tests for the Kanban WebUI and Dashboard REST API.

Tests cover:
1. POST /tasks: supports HTTP header X-Idempotency-Key and payload.idempotency_key.
2. POST /links: idempotent creation returns 200 OK with {"ok": True, "link_id": ..., "idempotent": True}.
3. POST /tasks/{task_id}/comments: duplicate comment suppression (recent identical comment or idempotency_key).
4. POST /tasks/{task_id}/reclaim: reclaiming a task already in 'ready' state returns idempotent success.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


def _load_plugin_router():
    repo_root = Path(__file__).resolve().parents[3]
    plugin_file = repo_root / "plugins" / "kanban" / "dashboard" / "plugin_api.py"
    assert plugin_file.exists(), f"plugin file missing: {plugin_file}"

    spec = importlib.util.spec_from_file_location(
        "hermes_dashboard_plugin_kanban_idempotency_test", plugin_file,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.router


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


@pytest.fixture
def client(kanban_home):
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/kanban")
    return TestClient(app)


def test_post_tasks_idempotency_via_header_and_payload(client):
    # 1. Via X-Idempotency-Key header
    header_key = "idem-header-12345"
    r1 = client.post(
        "/api/plugins/kanban/tasks",
        json={"title": "Task via header key", "body": "initial body"},
        headers={"X-Idempotency-Key": header_key},
    )
    assert r1.status_code == 200, r1.text
    task1_id = r1.json()["task"]["id"]

    # Repeat with same header
    r2 = client.post(
        "/api/plugins/kanban/tasks",
        json={"title": "Task via header key duplicate", "body": "different body"},
        headers={"X-Idempotency-Key": header_key},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["task"]["id"] == task1_id

    # 2. Via payload.idempotency_key
    payload_key = "idem-payload-67890"
    r3 = client.post(
        "/api/plugins/kanban/tasks",
        json={"title": "Task via payload key", "idempotency_key": payload_key},
    )
    assert r3.status_code == 200, r3.text
    task2_id = r3.json()["task"]["id"]

    r4 = client.post(
        "/api/plugins/kanban/tasks",
        json={"title": "Duplicate payload key", "idempotency_key": payload_key},
    )
    assert r4.status_code == 200, r4.text
    assert r4.json()["task"]["id"] == task2_id

    # 3. Payload key wins if both provided
    r5 = client.post(
        "/api/plugins/kanban/tasks",
        json={"title": "Both keys provided", "idempotency_key": payload_key},
        headers={"X-Idempotency-Key": "different-key"},
    )
    assert r5.status_code == 200
    assert r5.json()["task"]["id"] == task2_id


def test_post_links_idempotency(client):
    r_p = client.post("/api/plugins/kanban/tasks", json={"title": "Parent task"})
    r_c = client.post("/api/plugins/kanban/tasks", json={"title": "Child task"})
    parent_id = r_p.json()["task"]["id"]
    child_id = r_c.json()["task"]["id"]

    # First link creation
    r1 = client.post(
        "/api/plugins/kanban/links",
        json={"parent_id": parent_id, "child_id": child_id},
    )
    assert r1.status_code == 200, r1.text
    data1 = r1.json()
    assert data1["ok"] is True
    assert data1["link_id"] == f"{parent_id}:{child_id}"

    # Second link creation (idempotent duplicate)
    r2 = client.post(
        "/api/plugins/kanban/links",
        json={"parent_id": parent_id, "child_id": child_id},
    )
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["ok"] is True
    assert data2["link_id"] == f"{parent_id}:{child_id}"
    assert data2["idempotent"] is True


def test_post_comments_idempotency(client):
    r_t = client.post("/api/plugins/kanban/tasks", json={"title": "Task for comments"})
    task_id = r_t.json()["task"]["id"]

    # First comment
    r1 = client.post(
        f"/api/plugins/kanban/tasks/{task_id}/comments",
        json={"body": "Hello world", "author": "developer"},
    )
    assert r1.status_code == 200, r1.text
    data1 = r1.json()
    assert data1["ok"] is True
    comment_id = data1["comment_id"]

    # Duplicate comment sent immediately (within double-click window)
    r2 = client.post(
        f"/api/plugins/kanban/tasks/{task_id}/comments",
        json={"body": "Hello world", "author": "developer"},
    )
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["ok"] is True
    assert data2["comment_id"] == comment_id
    assert data2["idempotent"] is True

    # Duplicate comment with header / idempotency_key
    r3 = client.post(
        f"/api/plugins/kanban/tasks/{task_id}/comments",
        json={"body": "Hello world", "author": "developer", "idempotency_key": "c-key-1"},
    )
    assert r3.status_code == 200, r3.text
    data3 = r3.json()
    assert data3["ok"] is True
    assert data3["comment_id"] == comment_id
    assert data3["idempotent"] is True

    # Different comment is allowed
    r4 = client.post(
        f"/api/plugins/kanban/tasks/{task_id}/comments",
        json={"body": "Different message", "author": "developer"},
    )
    assert r4.status_code == 200, r4.text
    assert r4.json()["ok"] is True
    assert r4.json()["comment_id"] != comment_id
    assert r4.json().get("idempotent") is not True

    # Verify task only has 2 comments in DB, not 4
    detail = client.get(f"/api/plugins/kanban/tasks/{task_id}").json()
    assert len(detail["comments"]) == 2


def test_post_reclaim_idempotency_on_ready_task(client):
    r_t = client.post("/api/plugins/kanban/tasks", json={"title": "Task to reclaim"})
    task_id = r_t.json()["task"]["id"]

    # Task is initially ready (no worker running)
    detail = client.get(f"/api/plugins/kanban/tasks/{task_id}").json()
    assert detail["task"]["status"] == "ready"

    # Reclaim on ready task should succeed idempotently
    r1 = client.post(
        f"/api/plugins/kanban/tasks/{task_id}/reclaim",
        json={"reason": "Operator test"},
    )
    assert r1.status_code == 200, r1.text
    data1 = r1.json()
    assert data1["ok"] is True
    assert data1["task_id"] == task_id
    assert data1.get("idempotent") is True

    # Repeat reclaim
    r2 = client.post(
        f"/api/plugins/kanban/tasks/{task_id}/reclaim",
        json={},
    )
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["ok"] is True
    assert data2.get("idempotent") is True

    # Reclaim on unknown task still raises 409
    r_bad = client.post(
        "/api/plugins/kanban/tasks/t_unknown_missing/reclaim",
        json={},
    )
    assert r_bad.status_code == 409
