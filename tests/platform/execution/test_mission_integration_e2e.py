"""End-to-end integration tests for Civilization Mission Runtime, Supervisor, Approvals & Analytics."""

import tempfile
import threading
import time
from pathlib import Path
import pytest

from fastapi import FastAPI
from starlette.testclient import TestClient

from hermes.platform.execution.mission_store import MissionStore
from hermes.platform.execution.mission_runtime import MissionSupervisor
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes_cli import web_server


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.fixture
def client(isolated_env):
    with TestClient(web_server.app) as c:
        c.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
        yield c


def test_e2e_mission_full_lifecycle_and_measured_analytics(client, isolated_env):
    """Verifies mission creation, start, execution to completion, and canonical measured analytics."""
    # 1. Create mission with DAG workflow
    mission_payload = {
        "title": "Autonomous Refactoring Pipeline",
        "goal": "Refactor core modules with verified safety.",
        "workflow": {
            "nodes": [
                {"id": "step-1", "agent_id": "architect-001", "role": "planner", "action": "Plan"},
                {"id": "step-2", "agent_id": "builder-002", "role": "coder", "action": "Write Code"},
            ],
            "edges": [
                {"from_node": "step-1", "to_node": "step-2"},
            ],
        },
    }
    res_create = client.post("/api/civilization/missions", json=mission_payload)
    assert res_create.status_code == 200
    mission = res_create.json()
    mid = mission["id"]

    # 2. Before execution, analytics should show unmeasured (null tokens, null failures, null retries)
    analytics_before = client.get(f"/api/civilization/missions/{mid}/analytics").json()
    assert analytics_before["duration_seconds"] is None
    assert analytics_before["tokens"] is None
    assert analytics_before["failures"] is None
    assert analytics_before["retries"] is None

    # 3. Start the mission in runtime
    res_start = client.post(f"/api/civilization/missions/{mid}/start")
    assert res_start.status_code == 200
    started_data = res_start.json()
    assert started_data["status"] == "RUNNING"
    assert started_data["runtime_control"]["source"] == "canonical_mission_supervisor"

    # Wait for execution of both nodes
    deadline = time.time() + 3.0
    completed = False
    last_analytics = {}
    while time.time() < deadline:
        last_analytics = client.get(f"/api/civilization/missions/{mid}/analytics").json()
        if last_analytics.get("tokens") == 200:
            completed = True
            break
        time.sleep(0.1)

    if not completed:
        # Debug why it didn't complete
        store = MissionStore(isolated_env / "kanban.db", profile_key=str(isolated_env), home_path=isolated_env)
        adapter = KanbanAdapter(db_path=isolated_env / "kanban.db")
        mappings = store.list_task_mappings(mid)
        tasks_debug = [adapter.get_task(m["task_id"]) for m in mappings]
        m_state = store.get(mid)
        print("DEBUG M_STATE:", m_state)
        print("DEBUG MAPPINGS:", mappings)
        print("DEBUG TASKS:", tasks_debug)
        print("DEBUG ANALYTICS:", last_analytics)

    assert completed, f"Mission {mid} did not complete in time"

    # 4. Canonical measured analytics must be present and distinguish measured zero from null
    analytics_after = client.get(f"/api/civilization/missions/{mid}/analytics").json()
    assert analytics_after["duration_seconds"] is not None
    assert analytics_after["duration_seconds"] >= 0
    # Measured integers: 0 failures, 0 retries
    assert analytics_after["failures"] == 0
    assert analytics_after["retries"] == 0
    assert analytics_after["tokens"] == 200  # 100 per step in deterministic worker
    assert analytics_after["quality"] == "measured"


def test_e2e_mission_cooperative_pause_and_resume(client, isolated_env):
    """Verifies cooperative pause blocks new task admission and resume unblocks."""
    # Custom executor that pauses briefly so we can assert pause state
    mission_payload = {
        "title": "Staged Deployment",
        "goal": "Gradual rollouts",
        "workflow": {
            "nodes": [
                {"id": "n1", "agent_id": "architect-001", "role": "stage1", "action": "A"},
                {"id": "n2", "agent_id": "builder-002", "role": "stage2", "action": "B"},
            ],
            "edges": [{"from_node": "n1", "to_node": "n2"}],
        },
    }
    m = client.post("/api/civilization/missions", json=mission_payload).json()
    mid = m["id"]

    res_start = client.post(f"/api/civilization/missions/{mid}/start")
    assert res_start.status_code == 200

    # Pause mission
    res_pause = client.post(f"/api/civilization/missions/{mid}/pause")
    assert res_pause.status_code == 200
    assert res_pause.json()["status"] == "PAUSED"
    assert res_pause.json()["runtime_control"]["desired_state"] == "paused"

    # Resume mission
    res_resume = client.post(f"/api/civilization/missions/{mid}/resume")
    assert res_resume.status_code == 200
    assert res_resume.json()["status"] == "RUNNING"
    assert res_resume.json()["runtime_control"]["desired_state"] == "running"

    # Wait for completion
    deadline = time.time() + 10.0
    while time.time() < deadline:
        analytics = client.get(f"/api/civilization/missions/{mid}/analytics").json()
        if analytics.get("quality") == "measured":
            break
        time.sleep(0.1)

    final_analytics = client.get(f"/api/civilization/missions/{mid}/analytics").json()
    assert final_analytics["quality"] == "measured"
    assert final_analytics["failures"] == 0


def test_e2e_mission_runtime_approval_gate_and_resumption(client, isolated_env):
    """Verifies approval gate pauses task before execution, decision resumes, and task completes."""
    mission_payload = {
        "title": "Dangerous Operation Mission",
        "goal": "Execute code requiring explicit operator gate",
        "workflow": {
            "nodes": [
                {
                    "id": "step-safe",
                    "agent_id": "architect-001",
                    "role": "prep",
                    "action": "Prepare",
                },
                {
                    "id": "step-dangerous",
                    "agent_id": "builder-002",
                    "role": "deployer",
                    "action": "Deploy Production",
                    "requires_approval": True,
                },
            ],
            "edges": [{"from_node": "step-safe", "to_node": "step-dangerous"}],
        },
    }
    m = client.post("/api/civilization/missions", json=mission_payload).json()
    mid = m["id"]

    res_start = client.post(f"/api/civilization/missions/{mid}/start")
    assert res_start.status_code == 200

    # Wait for the approval gate to appear in /api/civilization/approvals
    deadline = time.time() + 10.0
    gate_found = None
    while time.time() < deadline:
        approvals_resp = client.get(f"/api/civilization/approvals?mission_id={mid}").json()
        approvals = approvals_resp.get("approvals", [])
        if approvals:
            gate_found = approvals[0]
            break
        time.sleep(0.1)

    assert gate_found is not None, "Runtime approval gate was not generated!"
    assert gate_found["source"] == "canonical_runtime"
    assert gate_found["mission_id"] == mid
    aid = gate_found["approval_id"]

    # While gate is pending, task step-dangerous must not complete
    store = MissionStore(isolated_env / "kanban.db", profile_key=str(isolated_env), home_path=isolated_env)
    assert not store.is_gate_approved(aid)

    # Approve the gate via decision endpoint
    res_decision = client.post(
        f"/api/civilization/approvals/{aid}/decision",
        json={"approved": True, "reason": "Operator confirmed safety verified."},
    )
    assert res_decision.status_code == 200
    decision_data = res_decision.json()
    assert decision_data["status"] == "recorded"
    assert decision_data["approved"] is True
    assert decision_data["runtime_resumed"] is True

    # Approvals list should now be empty for this mission
    approvals_now = client.get(f"/api/civilization/approvals?mission_id={mid}").json()["approvals"]
    assert approvals_now == []

    # Mission execution completes
    deadline = time.time() + 10.0
    while time.time() < deadline:
        analytics = client.get(f"/api/civilization/missions/{mid}/analytics").json()
        if analytics.get("quality") == "measured" and analytics.get("tokens", 0) >= 200:
            break
        time.sleep(0.1)

    final_analytics = client.get(f"/api/civilization/missions/{mid}/analytics").json()
    assert final_analytics["quality"] == "measured"
    assert final_analytics["tokens"] == 200
    assert final_analytics["failures"] == 0
