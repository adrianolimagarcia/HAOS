"""Acceptance gates for the blocked Rust writer contract v2.

These are black-box tests: every gate starts the real ``haos-edge`` binary, speaks HTTP to
the versioned writer route, and inspects the resulting ``state.db``. They do not read
crate source, do not weaken the original requirements, and never flip the writer cutover.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

CONTRACT_VERSION = "haos-edge.rust-writer.v2"
SCHEMA_VERSION = 2
WRITER_ROUTE = "/internal/rust-writer/v2/operations"

OPERATIONS = {
    "create_session",
    "append_messages",
    "update_session",
    "set_session_archived",
    "set_session_hidden",
    "set_session_pinned",
    "set_session_read",
    "set_session_title",
    "update_session_cwd",
    "update_session_meta",
    "set_message_reaction",
    "delete_session",
    "archive_and_compact",
}
FORBIDDEN_OPERATIONS = {"execute_sql", "query", "statement", "mutation"}
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(url: str, process: subprocess.Popen[str], timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"haos-edge exited early: {process.returncode}")
        try:
            urllib.request.urlopen(url, timeout=0.25).close()
            return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("health endpoint did not become ready")


def _stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _writer_server(tmp_path: Path, data_dir: Path | None = None) -> tuple[subprocess.Popen[str], int, str, Path]:
    binary = Path(__file__).resolve().parents[2] / "target/debug/haos-edge"
    assert binary.exists(), "build haos-edge before running writer black-box tests"
    data_dir = data_dir or (tmp_path / "writer-profile")
    data_dir.mkdir(parents=True, exist_ok=True)
    token = "writer-contract-ephemeral-token"
    port = _free_port()
    process = subprocess.Popen(
        [str(binary), "server", "--host", "127.0.0.1", "--port", str(port), "--profile", "writer-test",
         "--data-dir", str(data_dir), "--observer-only", "--writer-mode", "rust"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        env={**os.environ, "HAOS_RUST_WRITER_TOKEN": token, "HAOS_HOME": str(tmp_path / "home")},
    )
    try:
        _wait_health(f"http://127.0.0.1:{port}/health", process)
    except Exception:
        _stop(process)
        raise
    return process, port, token, data_dir


def _post(url: str, body: bytes, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()) if exc.headers.get("Content-Type", "").startswith("application/json") and exc.headers.get("Content-Length") != "0" else {}


def _post_json(url: str, envelope: dict, token: str | None) -> tuple[int, dict]:
    return _post(url, json.dumps(envelope).encode(), token)


def _envelope(operation: str, key: str, payload: dict, data_dir: Path, timeout_ms: int = 3000) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "profile": "writer-test",
        "data_dir": str(data_dir.resolve()),
        "idempotency_key": key,
        "timeout_ms": timeout_ms,
        "payload": payload,
    }


ERROR_CODES = {
    "invalid_request", "unsupported_contract_version", "unsupported_schema_version",
    "unknown_operation", "missing_idempotency_key", "invalid_idempotency_key",
    "invalid_timeout", "unauthenticated", "forbidden", "profile_mismatch",
    "data_dir_mismatch", "idempotency_conflict", "not_found", "timeout", "conflict",
    "busy", "internal", "storage_unavailable", "schema_mismatch", "read_only",
}



def test_contract_identity_and_closed_operation_registry():
    assert CONTRACT_VERSION == "haos-edge.rust-writer.v2"
    assert SCHEMA_VERSION == 2
    assert WRITER_ROUTE.startswith("/internal/")
    assert OPERATIONS
    assert not (OPERATIONS & FORBIDDEN_OPERATIONS)


def test_writer_route_is_present_and_versioned(tmp_path: Path):
    """The real route is mounted only for explicit Rust writer mode."""
    process, port, token, data_dir = _writer_server(tmp_path)
    url = f"http://127.0.0.1:{port}{WRITER_ROUTE}"
    state_db = data_dir / "state.db"
    try:
        status, payload = _post(url, b"{}", token)
        assert status == 400
        assert payload["error"]["code"] == "invalid_request"
        assert not state_db.exists()
    finally:
        _stop(process)


def test_startup_requires_explicit_profile_and_data_dir_binding(tmp_path: Path):
    """CLI refuses missing or invalid immutable binding before listener/DB creation."""
    binary = Path(__file__).resolve().parents[2] / "target/debug/haos-edge"
    data_dir = tmp_path / "must-not-be-created"
    cases = [
        [str(binary), "server", "--host", "127.0.0.1", "--port", str(_free_port()), "--profile", "writer-test"],
        [str(binary), "server", "--host", "127.0.0.1", "--port", str(_free_port()), "--profile", "../escape", "--data-dir", str(data_dir)],
    ]
    for command in cases:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        assert result.returncode != 0
    assert not data_dir.exists()


def test_internal_auth_is_checked_before_database_access(tmp_path: Path):
    """Missing and wrong bearer credentials fail before a state DB is created."""
    process, port, token, data_dir = _writer_server(tmp_path)
    url = f"http://127.0.0.1:{port}{WRITER_ROUTE}"
    try:
        for supplied in (None, "wrong-token"):
            status, payload = _post(url, b"{}", supplied)
            assert status == 401
            assert payload["error"]["code"] == "unauthenticated"
            assert not (data_dir / "state.db").exists()
    finally:
        _stop(process)


def test_typed_operations_reject_raw_sql_and_unknown_fields(tmp_path: Path):
    """HTTP validation rejects generic SQL and extra operation fields before DB access."""
    process, port, token, data_dir = _writer_server(tmp_path)
    url = f"http://127.0.0.1:{port}{WRITER_ROUTE}"
    base = {
        "contract_version": CONTRACT_VERSION, "schema_version": SCHEMA_VERSION,
        "profile": "writer-test", "data_dir": str(data_dir),
        "idempotency_key": "reject:1", "timeout_ms": 1000, "payload": {},
    }
    try:
        for operation in ("execute_sql", "set_session_title"):
            request = {**base, "operation": operation, "payload": {"session_id": "s", "title": "x", "sql": "DROP TABLE sessions"}}
            status, payload = _post(url, json.dumps(request).encode(), token)
            assert status == 400
            assert payload["error"]["code"] in {"unknown_operation", "invalid_request"}
            assert not (data_dir / "state.db").exists()
    finally:
        _stop(process)


def _initialize_schema(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    schema = Path(__file__).resolve().parents[2] / "docs/haos/state-db-schema.sql"
    with sqlite3.connect(data_dir / "state.db") as conn:
        conn.executescript(schema.read_text())
        conn.execute("""CREATE TABLE rust_writer_idempotency (
            profile TEXT NOT NULL, idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
            response_json TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY (profile, idempotency_key))""")


def _writer_request(data_dir: Path, key: str, title: str = "seed") -> bytes:
    return json.dumps({
        "contract_version": CONTRACT_VERSION, "schema_version": SCHEMA_VERSION,
        "profile": "writer-test", "data_dir": str(data_dir),
        "operation": "create_session", "idempotency_key": key, "timeout_ms": 5000,
        "payload": {"session_id": "writer-session", "source": "test", "title": title},
    }).encode()

def test_idempotency_is_atomic_and_detects_fingerprint_conflicts(tmp_path: Path):
    """HTTP retry returns stable result and conflicting reuse cannot mutate state."""
    data_dir = tmp_path / "writer-profile"
    _initialize_schema(data_dir)
    process, port, token, _ = _writer_server(tmp_path, data_dir)
    url = f"http://127.0.0.1:{port}{WRITER_ROUTE}"
    try:
        first = _post(url, _writer_request(data_dir, "create:1"), token)
        retry = _post(url, _writer_request(data_dir, "create:1"), token)
        assert first == retry
        conflict_status, conflict = _post(url, _writer_request(data_dir, "create:1", "different"), token)
        assert conflict_status == 409
        assert conflict["error"]["code"] == "idempotency_conflict"
        with sqlite3.connect(data_dir / "state.db") as conn:
            assert conn.execute("SELECT count(*) FROM sessions WHERE id='writer-session'").fetchone() == (1,)
            assert conn.execute("SELECT title FROM sessions WHERE id='writer-session'").fetchone() == ("seed",)
    finally:
        _stop(process)


def test_timeout_is_a_transactional_deadline_with_rollback(tmp_path: Path):
    """A locked DB returns contract timeout and leaves no partial mutation."""
    data_dir = tmp_path / "timeout-profile"
    _initialize_schema(data_dir)
    before = (data_dir / "state.db").read_bytes()
    with sqlite3.connect(data_dir / "state.db") as blocker:
        blocker.execute("BEGIN EXCLUSIVE")
        process, port, token, _ = _writer_server(tmp_path, data_dir)
        try:
            envelope = _envelope("create_session", "timeout-key-001", {
                "session_id": "timeout-session", "source": "webui",
            }, data_dir, timeout_ms=250)
            status, payload = _post_json(f"http://127.0.0.1:{port}{WRITER_ROUTE}", envelope, token)
            assert status == 408, payload
            assert payload["error"]["code"] == "timeout"
        finally:
            _stop(process)
            blocker.rollback()
    assert (data_dir / "state.db").read_bytes() == before
    with sqlite3.connect(data_dir / "state.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_error_codes_are_stable_and_machine_readable(tmp_path: Path):
    """Invalid contract inputs expose stable machine-readable HTTP errors."""
    process, port, token, data_dir = _writer_server(tmp_path)
    url = f"http://127.0.0.1:{port}{WRITER_ROUTE}"
    base = _envelope("create_session", "error:key-001", {"session_id": "s", "source": "test"}, data_dir)
    try:
        cases = [
            ({**base, "contract_version": "wrong"}, 400, "unsupported_contract_version"),
            ({**base, "schema_version": 999}, 400, "unsupported_schema_version"),
            ({**base, "operation": "execute_sql"}, 400, "unknown_operation"),
            ({**base, "profile": "other"}, 400, "profile_mismatch"),
        ]
        for request, expected_status, expected_code in cases:
            status, payload = _post_json(url, request, token)
            assert status == expected_status
            assert payload == {"ok": False, "error": {"code": expected_code}}
        status, payload = _post(url, b"{}", None)
        assert status == 401
        assert payload == {"ok": False, "error": {"code": "unauthenticated"}}
    finally:
        _stop(process)


def test_writer_is_the_only_state_db_mutation_authority_before_cutover(tmp_path: Path):
    """Only explicit rust mode exposes v2; observer/python modes reject write routes."""
    binary = Path(__file__).resolve().parents[2] / "target/debug/haos-edge"
    for mode, observer_only in (("python", True), ("rust", True)):
        data_dir = tmp_path / f"{mode}-profile"
        data_dir.mkdir()
        port = _free_port()
        env = {**os.environ, "HAOS_HOME": str(tmp_path / "home")}
        if mode == "rust":
            env["HAOS_RUST_WRITER_TOKEN"] = "writer-contract-ephemeral-token"
        process = subprocess.Popen(
            [str(binary), "server", "--host", "127.0.0.1", "--port", str(port), "--profile", "authority-test",
             "--data-dir", str(data_dir), "--writer-mode", mode] + (["--observer-only"] if observer_only else []),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, env=env,
        )
        try:
            _wait_health(f"http://127.0.0.1:{port}/health", process)
            request = _envelope("create_session", f"authority:{mode}", {"session_id": "s", "source": "test"}, data_dir)
            token = env.get("HAOS_RUST_WRITER_TOKEN")
            status, _ = _post_json(f"http://127.0.0.1:{port}{WRITER_ROUTE}", request, token)
            assert status == (400 if mode == "rust" else 404)
            assert not (data_dir / "state.db").exists()
            ingest = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/events/ingest", data=b"{}",
                headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                with urllib.request.urlopen(ingest, timeout=2) as response:
                    ingest_status = response.status
            except urllib.error.HTTPError as exc:
                ingest_status = exc.code
            assert ingest_status in (401, 404)
            assert not (data_dir / "state.db").exists()
        finally:
            _stop(process)
