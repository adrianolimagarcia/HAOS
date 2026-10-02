"""Live REST autonomy contract: read-only inspection, strict admission and profiles."""
import json
import time
from pathlib import Path

from starlette.testclient import TestClient


def setup_client(tmp_path, monkeypatch):
    from hermes_cli import web_server
    home = tmp_path / ".haos"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HAOS_HOME", str(home))
    client = TestClient(web_server.app)
    return home, client, web_server


def test_autonomy_readonly_auth_and_strict_mutations(tmp_path, monkeypatch):
    home, client, server = setup_client(tmp_path, monkeypatch)
    assert client.get("/api/framework/autonomy").status_code == 401
    assert client.post("/api/framework/autonomy/pause", json={}).status_code == 401
    assert client.post("/api/framework/autonomy/events", json={}).status_code == 401
    client.headers[server._SESSION_HEADER_NAME] = server._SESSION_TOKEN
    result = client.get("/api/framework/autonomy")
    assert result.status_code == 200, result.text
    assert result.json()["queue"]["available"] is False
    assert result.json()["service"]["status"] == "unavailable"
    assert not (home / "agent").exists()
    assert not (home / "config.yaml").exists()
    for body in ({"paused": True}, {"paused": "true", "confirm": True}, {"paused": True, "confirm": True, "command": "x"}):
        assert client.post("/api/framework/autonomy/pause", json=body).status_code == 422
    assert client.post("/api/framework/autonomy/pause", json={"paused": True, "confirm": True}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
    event = {"type": "operator.investigation", "source": "dashboard", "severity": "info", "payload": {"summary": "Observed a timeout"}}
    for body in ({**event, "command": "echo x"}, {**event, "type": "command.run"}, {**event, "payload": {"summary": "x", "command": "echo x"}}):
        assert client.post("/api/framework/autonomy/events", json=body).status_code == 422
    assert client.post("/api/framework/autonomy/pause", json={"paused": True, "confirm": True}).json() == {"paused": True}
    result = client.post("/api/framework/autonomy/events", json=event)
    assert result.status_code == 200, result.text
    assert result.json()["accepted"] is True
    snapshot = client.get("/api/framework/autonomy").json()
    assert snapshot["queue"]["paused"] is True
    assert snapshot["queue"]["recent_jobs"][0]["status"] == "pending"
    assert not (home / "agent" / "plans").exists()  # Admission never invokes AI or plans.
    heartbeat = home / "agent" / "autonomy" / "service.json"
    heartbeat.write_text(json.dumps({"status": "running", "updated_at": time.time() - 121, "pid": 1}))
    assert client.get("/api/framework/autonomy").json()["service"]["status"] == "stale"


def test_autonomy_live_profile_isolation_and_redirect_refusal(tmp_path, monkeypatch):
    _, client, server = setup_client(tmp_path, monkeypatch)
    from hermes_cli.profiles import _get_profiles_root
    homes = {}
    for name in ("a", "b"):
        home = _get_profiles_root() / name
        home.mkdir(parents=True)
        (home / "config.yaml").write_text(json.dumps({"framework": {"autonomy": {"enabled": name == "a"}}}))
        homes[name] = home
    client.headers[server._SESSION_HEADER_NAME] = server._SESSION_TOKEN
    assert client.post("/api/framework/autonomy/pause?profile=a", json={"paused": True, "confirm": True}).status_code == 200
    for name in ("a", "b", "a"):
        result = client.get(f"/api/framework/autonomy?profile={name}")
        assert result.status_code == 200, result.text
        assert result.json()["config"]["enabled"] is (name == "a")
        assert result.json()["queue"]["paused"] is (name == "a")
    assert not (homes["b"] / "agent").exists()
    assert client.get("/api/framework/autonomy?profile=missing").status_code == 404
    (homes["b"] / "agent").mkdir()
    (homes["b"] / "agent" / "autonomy").symlink_to(homes["a"] / "agent" / "autonomy", target_is_directory=True)
    assert client.get("/api/framework/autonomy?profile=b").status_code == 409
    assert client.post("/api/framework/autonomy/pause?profile=b", json={"paused": False, "confirm": True}).status_code == 409
