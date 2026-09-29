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

