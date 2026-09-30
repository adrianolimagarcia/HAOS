"""Behavioral read-only Civilization dashboard API integration tests."""

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from starlette.testclient import TestClient

from hermes.platform.bots.identity import BotIdentityBundle, CouncilSpec
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.spec import BotSpec
from hermes.platform.council.manager import CouncilManager
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event


@pytest.fixture
def homes(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    other = root / "profiles" / "other"
    other.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(root))
    return root, other


@pytest.fixture
def client():
    from hermes_cli import web_server

    c = TestClient(web_server.app)
    c.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    return c


def test_empty_scoped_overview_and_auth_do_not_create_database(client, homes):
    root, other = homes
    from hermes_cli import web_server

    unauthorized = TestClient(web_server.app).get("/api/civilization/overview?profile=other")
    assert unauthorized.status_code == 401
    for profile in (None, "other", None):
        response = client.get("/api/civilization/overview", params={"profile": profile} if profile else {})
        assert response.status_code == 200, response.text
        assert response.json() == {"bots": [], "councils": [], "leaves": [],
                                   "decisions": [], "proposals": [], "constitution": None,
                                   "reputation": [], "events": [], "cursor": 0}
    assert not (root / "events.db").exists()
    assert not (other / "events.db").exists()
    assert client.get("/api/civilization/overview?profile=../../escape").status_code == 400


def test_real_manager_projections_are_profile_scoped_and_redacted(client, homes):
    root, other = homes
    secret = "PRIVATE SOUL material should never leave storage"
    path = root / "events.db"
    with EventStore(str(path)) as store:
        bots = BotSpecManager(store)
        bots.register(BotSpec(id="bot-a", name="Alpha"))
        bots.pause("bot-a")
        IdentityManager(store).create_version("bot-a", BotIdentityBundle(bot_id="bot-a", soul=secret))
        council = CouncilManager(store)
        council.register(CouncilSpec(id="council-a", purpose="Planning", members=["bot-a", "bot-b"]))
        session = council.start_session("council-a", "Review")
        council.submit_position(session.session_id, "bot-a", "Approve")
        decision = council.record_decision(session.session_id, "Agreed", "Approve")
        store.append(Event(name="civ.leaf.created", payload={"leaf": {
            "leaf_id": "leaf-a", "parent_bot_id": "bot-a", "worktree_path": "/private/leaf",
            "identity_snapshot": {"temporary_soul": secret, "council_id": "council-a"},
        }}))
        store.append(Event(name="civ.leaf.completed", payload={"leaf_id": "leaf-a", "summary": secret}))
        store.append(Event(name="civ.leaf.failed", payload={"leaf_id": "not-created", "error": secret}))
        expected_cursor = store.cursor()
    before = path.stat().st_mtime_ns
    for profile in (None, "other", None):
        response = client.get("/api/civilization/overview", params={"profile": profile} if profile else {})
        assert response.status_code == 200, response.text
        body = response.json()
        if profile:
            assert body["bots"] == body["events"] == body["leaves"] == []
            continue
        assert body["cursor"] == expected_cursor
        assert body["bots"][0]["bot_id"] == "bot-a"
        assert body["bots"][0]["status"] == "paused"
        assert body["bots"][0]["version"] == 1
        assert body["councils"][0]["members"] == ["bot-a", "bot-b"]
        assert body["decisions"] == [{"decision_id": decision.id, "council_id": "council-a",
                                       "council_session_id": session.session_id,
                                       "created_at": decision.created_at}]
        assert any(e["name"] == "civ.council.decision-recorded"
                   and e["decision_id"] == decision.id for e in body["events"])
        assert body["leaves"] == [{"leaf_id": "leaf-a", "parent_bot_id": "bot-a",
                                   "council_id": "council-a", "council_session_id": None,
                                   "status": "completed"}]
        assert any(e["name"] == "civ.leaf.completed" and e["leaf_id"] == "leaf-a"
                   for e in body["events"])
        assert secret not in response.text
        assert "/private/leaf" not in response.text
    assert path.stat().st_mtime_ns == before
    assert not (other / "events.db").exists()


def test_civilization_graph_projection_endpoint(client, homes):
    root, _ = homes
    path = root / "events.db"
    with EventStore(str(path)) as store:
        bots = BotSpecManager(store)
        bots.register(BotSpec(id="bot-x", name="X Bot"))
        bots.register(BotSpec(id="bot-y", name="Y Bot"))
        council = CouncilManager(store)
        council.register(CouncilSpec(id="council-x", purpose="Coordination", members=["bot-x", "bot-y"]))
        session = council.start_session("council-x", "Plan")
        council.submit_position(session.session_id, "bot-x", "Ready")
        council.submit_position(session.session_id, "bot-y", "Ready")
        council.record_decision(session.session_id, "Done", "Approve")

    res = client.get("/api/civilization/graph")
    assert res.status_code == 200
    data = res.json()
    assert data["schema_version"] == 1
    assert data["counts"]["bots"] >= 2
    assert any(n["id"] == "bot:bot-x" for n in data["nodes"])
    assert any(n["id"] == "council:council-x" for n in data["nodes"])
    assert any(e["source"] == "council:council-x" and e["target"] == "bot:bot-x" for e in data["edges"])


def test_council_memory_endpoints(client, homes):
    root, _ = homes
    path = root / "events.db"
    with EventStore(str(path)) as store:
        council = CouncilManager(store)
        council.register(CouncilSpec(id="gov-council", purpose="Governance", members=["bot-g1", "bot-g2"]))
        session = council.start_session("gov-council", "Charter review")
        council.submit_position(session.session_id, "bot-g1", "Accept")
        council.submit_position(session.session_id, "bot-g2", "Accept")
        council.record_decision(session.session_id, "Accepted charter", "Adopt")

    # GET memory
    res = client.get("/api/civilization/councils/gov-council/memory")
    assert res.status_code == 200
    body = res.json()
    assert body["record"]["council_id"] == "gov-council"
    assert "Governance" in body["content"]

    # POST rebuild memory
    res_rebuild = client.post("/api/civilization/councils/gov-council/memory/rebuild")
    assert res_rebuild.status_code == 200
    assert res_rebuild.json()["status"] == "rebuilt"


def test_evolution_curator_endpoints(client, homes):
    root, _ = homes
    path = root / "events.db"
    with EventStore(str(path)):
        pass

    # GET status
    res = client.get("/api/civilization/evolution/curator/status")
    assert res.status_code == 200
    assert "last_processed_seq" in res.json()

    # POST run cycle
    res_run = client.post("/api/civilization/evolution/curator/run", json={"dry_run": True})
    assert res_run.status_code == 200
    assert res_run.json()["status"] == "completed"
    assert res_run.json()["dry_run"] is True


def test_civilization_control_plane_agents(client, homes):
    root, other = homes

    # 1. Available Models
    res_models = client.get("/api/civilization/models/available")
    assert res_models.status_code == 200
    models_data = res_models.json()
    assert "models" in models_data
    assert len(models_data["models"]) >= 5
    assert any(m["id"] == "deepseek-v4-flash" for m in models_data["models"])

    # 2. List initial agents with model resolution
    res_agents = client.get("/api/civilization/agents")
    assert res_agents.status_code == 200
    agents_data = res_agents.json()
    assert "agents" in agents_data
    assert len(agents_data["agents"]) >= 5
    arch = next(a for a in agents_data["agents"] if a["id"] == "architect-001")
    assert arch["effective_model"] == "deepseek-v4-flash"
    assert arch["model_source"] in ("SESSION_MODEL", "GLOBAL_DEFAULT")

    # 3. Constitutional Invariant: Non-PROMOTER cannot write to global_civ memory
    illegal_payload = {
        "id": "illegal-agent",
        "name": "Illegal Agent",
        "role": "builder",
        "domain": "Testing",
        "description": "Attempting illegal global memory write",
        "status": "active",
        "model": {"inherit": True},
        "capabilities": {"allowed_tools": ["read_file"], "max_risk_tier": "LOW", "allowed_write_paths": []},
        "memory": {"working": True, "session": True, "project": True, "domain": False, "global_civ": True},
        "budget": {"max_tokens": 100000, "timeout_seconds": 600, "max_cost_usd": 2.0, "max_iterations": 10},
        "policies": {"require_human_approval": [], "risk_tolerance": "CONSERVATIVE"},
    }
    res_illegal = client.post("/api/civilization/agents", json=illegal_payload)
    assert res_illegal.status_code == 400
    assert "strictly forbidden from writing to GLOBAL memory scope" in res_illegal.text

    # 4. Valid Agent Creation with Agent Override
    valid_payload = {
        "id": "code-auditor",
        "name": "Code Auditor",
        "role": "critic",
        "domain": "Security",
        "description": "Specialized security reviewer",
        "status": "active",
        "model": {"inherit": False, "model_name": "claude-3-7-sonnet"},
        "capabilities": {"allowed_tools": ["read_file", "git"], "max_risk_tier": "READ", "allowed_write_paths": []},
        "memory": {"working": True, "session": True, "project": True, "domain": False, "global_civ": False},
        "budget": {"max_tokens": 100000, "timeout_seconds": 600, "max_cost_usd": 2.0, "max_iterations": 10},
        "policies": {"require_human_approval": ["merge"], "risk_tolerance": "CONSERVATIVE"},
    }
    res_create = client.post("/api/civilization/agents", json=valid_payload)
    assert res_create.status_code == 200
    created = res_create.json()
    assert created["id"] == "code-auditor"
    assert created["effective_model"] == "claude-3-7-sonnet"
    assert created["model_source"] == "AGENT_OVERRIDE"

    # 5. Detail endpoint
    res_detail = client.get("/api/civilization/agents/code-auditor")
    assert res_detail.status_code == 200
    assert res_detail.json()["id"] == "code-auditor"

    # 6. Update agent
    valid_payload["description"] = "Updated security description"
    res_update = client.put("/api/civilization/agents/code-auditor", json=valid_payload)
    assert res_update.status_code == 200
    assert res_update.json()["description"] == "Updated security description"

    # 7. Duplicate agent
    res_dup = client.post("/api/civilization/agents/code-auditor/duplicate")
    assert res_dup.status_code == 200
    dup_agent = res_dup.json()
    assert dup_agent["id"] == "code-auditor-copy"
    assert "Copy" in dup_agent["name"]

    # 8. Export agent
    res_export = client.post("/api/civilization/agents/code-auditor/export")
    assert res_export.status_code == 200
    assert res_export.json()["id"] == "code-auditor"

    # 9. Import agent
    imported_payload = dict(valid_payload)
    imported_payload["id"] = "imported-bot"
    imported_payload["name"] = "Imported Bot"
    res_import = client.post("/api/civilization/agents/import", json=imported_payload)
    assert res_import.status_code == 200
    assert res_import.json()["id"] == "imported-bot"


def test_civilization_mission_center(client, homes):
    # 1. List Missions
    res_missions = client.get("/api/civilization/missions")
    assert res_missions.status_code == 200
    missions_list = res_missions.json()["missions"]
    assert len(missions_list) >= 1
    m0 = missions_list[0]
    mission_id = m0["id"]

    # 2. Get Mission Detail
    res_detail = client.get(f"/api/civilization/missions/{mission_id}")
    assert res_detail.status_code == 200
    assert res_detail.json()["id"] == mission_id

    # 3. Simulate Mission (Dry-Run via SimulationEngine)
    res_sim = client.post(f"/api/civilization/missions/{mission_id}/simulate")
    assert res_sim.status_code == 200
    sim_data = res_sim.json()
    assert "simulation_id" in sim_data
    assert "estimated_tokens" in sim_data
    assert "estimated_cost_usd" in sim_data
    assert "blast_radius" in sim_data
    assert "predicted_steps" in sim_data

    # 4. Start Mission
    res_start = client.post(f"/api/civilization/missions/{mission_id}/start")
    assert res_start.status_code == 200
    assert res_start.json()["status"] == "RUNNING"

    # 5. Pause Mission
    res_pause = client.post(f"/api/civilization/missions/{mission_id}/pause")
    assert res_pause.status_code == 200
    assert res_pause.json()["status"] == "PAUSED"

    # 6. Resume Mission
    res_resume = client.post(f"/api/civilization/missions/{mission_id}/resume")
    assert res_resume.status_code == 200
    assert res_resume.json()["status"] == "RUNNING"

    # 7. Get Timeline Events
    res_events = client.get(f"/api/civilization/missions/{mission_id}/events")
    assert res_events.status_code == 200
    assert len(res_events.json()["events"]) >= 3

    # 8. Create Custom Mission
    new_mission_payload = {
        "title": "Build Realtime WebSocket Gateway",
        "goal": "Implement asynchronous WebSocket bridge with heartbeats.",
        "agents": ["architect-001", "builder-002"],
    }
    res_create_m = client.post("/api/civilization/missions", json=new_mission_payload)
    assert res_create_m.status_code == 200
    new_m = res_create_m.json()
    assert new_m["title"] == "Build Realtime WebSocket Gateway"
    assert new_m["status"] == "DRAFT"

    # 9. Record Human Approval Decision
    res_appr = client.post("/api/civilization/approvals/appr-123/decision", json={
        "approved": True,
        "reason": "Reviewed code and test coverage looks great."
    })
    assert res_appr.status_code == 200
    assert res_appr.json()["status"] == "recorded"
    assert res_appr.json()["approved"] is True
