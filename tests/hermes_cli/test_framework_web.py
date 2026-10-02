"""Real canonical REST integration: auth, dry-run truth and A→B→A isolation."""
import json
from pathlib import Path

from starlette.testclient import TestClient


def test_framework_auth_and_dry_run_contract(tmp_path, monkeypatch):
    from hermes_cli import web_server
    home = tmp_path / ".haos"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HAOS_HOME", str(home))
    client = TestClient(web_server.app)
    for route in ("status", "telemetry", "plans", "hierarchy", "actions"):
        assert client.get(f"/api/framework/{route}").status_code == 401
    for route in ("run", "grant", "approve", "apply"):
        assert client.post(f"/api/framework/{route}", json={}).status_code == 401
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    assert client.get("/api/framework/status").json()["initialized"] is False
    assert not (home / "agent").exists()
    assert client.post("/api/framework/run", json={"dry_run": False}).status_code == 422
    assert client.post("/api/framework/run", json={"autonomous": True}).status_code == 422
    response = client.post("/api/framework/run", json={"dry_run": True})
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["dry_run"] is True
    assert record["verification"]["verified"] is False
    assert all(not result["success"] for result in record["execution_results"])
    assert client.get("/api/framework/actions").json()["cycles"][0]["cycle_id"] == record["cycle_id"]
    assert client.get("/api/framework/hierarchy").status_code == 200
    assert client.post("/api/framework/run", json={}, headers={"Origin": "https://attacker.invalid"}).status_code == 403

    def preview():
        result = client.post("/api/framework/run", json={"create_report": True}).json()
        plan = result["plan"]
        return {"plan_id": plan["plan_id"], "step_id": plan["steps"][0]["step_id"], "confirm": True}

    from hermes_cli.web_routers.framework import _authenticate
    from fastapi import HTTPException
    from starlette.requests import Request
    import pytest
    monkeypatch.setattr(web_server.app.state, "auth_required", True, raising=False)
    def cookie_request(origin=None):
        headers = [(b"host", b"testserver"), (b"authorization", b"Bearer invalid")]
        if origin: headers.append((b"origin", origin.encode()))
        return Request({"type": "http", "method": "POST", "path": "/api/framework/grant", "headers": headers, "app": web_server.app, "state": {"session": object()}})
    for origin in (None, "https://attacker.invalid", "http://testserver/path"):
        with pytest.raises(HTTPException) as denied:
            _authenticate(cookie_request(origin))
        assert denied.value.status_code == 403
    _authenticate(cookie_request("http://testserver"))
    monkeypatch.setattr(web_server.app.state, "auth_required", False)

    identity = preview()
    assert client.post("/api/framework/grant", json=identity).status_code == 200
    approval = client.post("/api/framework/approve", json=identity).json()
    apply_body = {"plan_id": identity["plan_id"], "approval_ids": [approval["approval_id"]], "confirm": True}
    assert client.post("/api/framework/apply", json=apply_body).status_code == 200
    artifact = home / "agent" / "artifacts" / "operational-report.json"
    assert artifact.is_file()
    assert client.get("/api/framework/actions").json()["receipts"][0]["phase"] == "finished"
    assert client.post("/api/framework/apply", json=apply_body).status_code == 409

    # Expiry and tampered intent fail closed without accepting client approval objects.
    identity = preview()
    assert client.post("/api/framework/grant", json=identity).status_code == 200
    approval = client.post("/api/framework/approve", json=identity).json()
    path = home / "agent" / "approvals" / f'{approval["approval_id"]}.json'
    approval["expires_at"] = 0
    path.write_text(json.dumps(approval))
    assert client.post("/api/framework/apply", json={"plan_id": identity["plan_id"], "approval_ids": [approval["approval_id"]], "confirm": True}).status_code == 409
    identity = preview()
    assert client.post("/api/framework/grant", json=identity).status_code == 200
    approval = client.post("/api/framework/approve", json=identity).json()
    path = home / "agent" / "plans" / f'{identity["plan_id"]}.json'
    record = json.loads(path.read_text())
    record["plan"]["steps"][0]["params"]["content"] = {"tampered": True}
    path.write_text(json.dumps(record))
    assert client.post("/api/framework/apply", json={"plan_id": identity["plan_id"], "approval_ids": [approval["approval_id"]], "confirm": True}).status_code == 409
    assert client.post("/api/framework/apply", json={"plan_id": identity["plan_id"], "approvals": [approval], "confirm": True}).status_code == 422


def test_framework_profile_isolation_and_fail_closed(tmp_path, monkeypatch):
    from hermes_cli import web_server
    from hermes_cli.profiles import _get_profiles_root
    home = tmp_path / ".haos"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HAOS_HOME", str(home))
    homes = {}
    for name in ("a", "b"):
        scoped = _get_profiles_root() / name
        scoped.mkdir(parents=True)
        (scoped / "config.yaml").write_text("{}")
        state = scoped / "agent" / "state"
        state.mkdir(parents=True)
        (state / "current.yaml").write_text(json.dumps({"cycle_id": name}))
        homes[name] = scoped
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    for name in ("a", "b", "a"):
        response = client.get("/api/framework/status", params={"profile": name})
        assert response.status_code == 200, response.text
        assert response.json()["state"]["cycle_id"] == name
    result = client.post("/api/framework/run?profile=a", json={"create_report": True}).json()
    identity = {"plan_id": result["plan"]["plan_id"], "step_id": result["plan"]["steps"][0]["step_id"], "confirm": True}
    assert client.post("/api/framework/grant?profile=a", json=identity).status_code == 200
    approval = client.post("/api/framework/approve?profile=a", json=identity).json()
    assert client.post("/api/framework/apply?profile=b", json={"plan_id": identity["plan_id"], "approval_ids": [approval["approval_id"]], "confirm": True}).status_code == 409
    assert not (homes["b"] / "agent" / "artifacts" / "operational-report.json").exists()
    assert client.get("/api/framework/status?profile=missing").status_code == 404
    assert client.get("/api/framework/status?profile=../A").status_code in (400, 404)
    state = homes["b"] / "agent" / "state" / "current.yaml"
    state.unlink()
    state.symlink_to(homes["a"] / "agent" / "state" / "current.yaml")
    assert client.get("/api/framework/status?profile=b").status_code == 409
