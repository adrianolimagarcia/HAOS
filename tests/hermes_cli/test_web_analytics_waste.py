"""GET /api/analytics/waste — detectors fire on crafted transcripts, stay silent on clean ones.

Every assertion checks a MEASURED number (exact char sums over stored rows); the
honesty contract (null when unmeasurable, no fabricated savings) is part of the
contract under test.
"""
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli.web_routers import analytics
from hermes_state import SessionDB


@pytest.fixture()
def home_db(tmp_path, monkeypatch):
    """A temp HERMES_HOME state.db reachable through the late-bound profile helper."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    monkeypatch.setattr(hermes_state, "_default_db_path", lambda: str(db_path))
    yield db
    db.close()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(analytics.router)
    return TestClient(app)


def _err(content: str) -> str:
    return json.dumps({"output": content, "exit_code": 1, "error": None})


def _ok(content: str) -> str:
    return json.dumps({"output": content, "exit_code": 0, "error": None})


def _tool_calls(cid: str, name: str, args: dict) -> list:
    return [{"id": cid, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args, sort_keys=True)}}]


def test_user_loop_detected_with_measured_chars(home_db, client):
    home_db.create_session("loop", source="cli", model="m")
    texts = ["revisa o deploy por favor"] * 5
    for i, t in enumerate(texts):
        home_db.append_message("loop", "user", t, timestamp=time.time() + i)
    # a dissimilar message must break the streak
    home_db.append_message("loop", "user", "outro assunto completamente distinto agora",
                           timestamp=time.time() + 9)

    body = client.get("/api/analytics/waste?days=30").json()
    loops = [f for f in body["findings"] if f["kind"] == "user_loop"]
    assert len(loops) == 1
    assert loops[0]["session_id"] == "loop"
    assert loops[0]["count"] == 5
    assert loops[0]["measured_chars"] == sum(len(t) for t in texts)
    assert loops[0]["approx_tokens"] == round(loops[0]["measured_chars"] / 4)


def test_synthetic_user_rows_never_count_as_loops(home_db, client):
    home_db.create_session("synth", source="cli", model="m")
    base = time.time()
    for i in range(6):
        # background-process re-injection: identical template, machine-authored
        home_db.append_message(
            "synth", "user",
            f"[IMPORTANT: Background process proc_abc completed normally (exit code 0). run {i}]",
            timestamp=base + i)
    for i in range(6):
        # compaction handoff carrier
        home_db.append_message("synth", "user", "[CONTEXT COMPACTION — REFERENCE ONLY] identical body",
                               _compressed_summary=True, timestamp=base + 20 + i)

    body = client.get("/api/analytics/waste?days=30").json()
    assert [f for f in body["findings"] if f["kind"] == "user_loop"] == []


def test_tool_cascade_and_retry_churn(home_db, client):
    home_db.create_session("bad", source="cli", model="m")
    base = time.time()
    args = {"command": "make -C /kernel"}
    payloads = []
    for i in range(4):
        cid = f"call_{i}"
        content = _err("make: *** [Makefile:1] Error 2")
        payloads.append(content)
        home_db.append_message("bad", "assistant", None,
                               tool_calls=_tool_calls(cid, "terminal", args), timestamp=base + i * 2)
        home_db.append_message("bad", "tool", content, tool_name="terminal",
                               tool_call_id=cid, timestamp=base + i * 2 + 1)
    # a success breaks the cascade streak
    home_db.append_message("bad", "assistant", None,
                           tool_calls=_tool_calls("call_ok", "terminal", {"command": "ls"}),
                           timestamp=base + 20)
    home_db.append_message("bad", "tool", _ok("file"), tool_name="terminal",
                           tool_call_id="call_ok", timestamp=base + 21)

    body = client.get("/api/analytics/waste?days=30").json()
    findings = {f["kind"]: f for f in body["findings"] if f["session_id"] == "bad"}
    assert findings["tool_cascade"]["count"] == 4
    assert findings["tool_cascade"]["measured_chars"] == sum(len(p) for p in payloads)
    assert findings["retry_churn"]["count"] == 4  # same tool + same args, all failing
    assert findings["retry_churn"]["evidence"].startswith("terminal(")


def test_clean_session_yields_no_findings(home_db, client):
    home_db.create_session("clean", source="cli", model="m")
    base = time.time()
    for i in range(6):
        home_db.append_message("clean", "user", f"pergunta numero {i} totalmente diferente {i*i}",
                               timestamp=base + i * 3)
        home_db.append_message("clean", "assistant", None,
                               tool_calls=_tool_calls(f"c{i}", "read_file", {"path": f"/f{i}"}),
                               timestamp=base + i * 3 + 1)
        home_db.append_message("clean", "tool", _ok("data"), tool_name="read_file",
                               tool_call_id=f"c{i}", timestamp=base + i * 3 + 2)

    body = client.get("/api/analytics/waste?days=30").json()
    assert [f for f in body["findings"] if f["session_id"] == "clean"] == []


def test_cache_hit_ratio_is_measured(home_db, client):
    home_db.create_session("s1", source="cli", model="alpha")
    home_db._conn.execute(
        "UPDATE sessions SET input_tokens=1000, cache_read_tokens=3000, cache_write_tokens=0 WHERE id='s1'")
    home_db._conn.commit()

    body = client.get("/api/analytics/waste?days=30").json()
    model = {m["model"]: m for m in body["cache_hit"]["by_model"]}["alpha"]
    assert model["hit_ratio"] == pytest.approx(0.75)
    assert body["cache_hit"]["overall"]["hit_ratio"] == pytest.approx(0.75)


def test_occupancy_reports_unmeasured_not_zero(home_db, client):
    home_db.create_session("s1", source="cli", model="alpha")
    home_db.append_message("s1", "user", "oi")  # token_count stays NULL
    body = client.get("/api/analytics/waste?days=30").json()
    occ = body["live_context_occupancy"]
    assert occ["available"] is False
    assert "token_count" in occ["reason"]


def test_max_sessions_bounds_scan_and_flags_truncation(home_db, client):
    for i in range(5):
        home_db.create_session(f"s{i}", source="cli", model="m")
        home_db._conn.execute(
            "UPDATE sessions SET input_tokens=?, output_tokens=0 WHERE id=?", (1000 + i, f"s{i}"))
    home_db._conn.commit()
    body = client.get("/api/analytics/waste?days=30&max_sessions=3").json()
    assert body["sessions_scanned"] == 3
    assert body["truncated"] is True


def test_waste_route_gates_corrupt_store(tmp_path, client):
    from pathlib import Path
    import sqlite3
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    db.create_session("s1", source="cli", model="m")
    db.close()
    for side in ("-wal", "-shm"):
        Path(str(db_path) + side).unlink(missing_ok=True)
    with open(db_path, "r+b") as f:
        f.seek(100)
        f.write(b"\xff" * (4096 - 100))
    import hermes_state
    hermes_state._default_db_path = lambda: str(db_path)
    resp = client.get("/api/analytics/waste?days=30")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "state_db_corrupt"
