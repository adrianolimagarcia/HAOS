"""Acceptance tests for the blocked Rust read-only SSE slice.

These are contract tests, not source-shape checks. They use a real SQLite fixture and the
real profile resolver/reference store. The HTTP checks are marked integration because the
current crate does not expose an in-process router builder.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import sqlite3
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

CONTRACT_VERSION = "haos-edge.readonly-sse.v1"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_observer(binary: Path, tmp_path: Path, profile: str, data_dir: Path,
                    extra_args: list[str] | None = None) -> tuple[subprocess.Popen[str], int]:
    port = _free_port()
    process = subprocess.Popen(
        [str(binary), "server", "--host", "127.0.0.1", "--port", str(port), "--profile", profile,
         "--data-dir", str(data_dir), "--observer-only", "--writer-mode", "python",
         *(extra_args or [])],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        env={**os.environ, "HAOS_HOME": str(tmp_path / "home")},
    )
    _wait_health(f"http://127.0.0.1:{port}/health", process)
    return process, port


def _stop_process(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _authenticated_cookie(data_dir: Path) -> str:
    """Seed a disposable strict-auth session matching the production verifier."""
    salt = b"sse-contract-test"
    password = b"ephemeral-test-password"
    digest = hashlib.pbkdf2_hmac("sha256", password, salt, 200_000, dklen=32)
    encode = lambda value: value.hex()
    (data_dir / "webui.passwd").write_text(
        f"pbkdf2_sha256$200000${encode(salt)}${encode(digest)}\n"
    )
    sessions = data_dir / "sessions"
    sessions.mkdir(mode=0o700)
    token = "a" * 64
    (sessions / token).write_text("False\n")
    return f"haos_session={token}"


def _get_sse(url: str, cookie: str) -> tuple[dict[str, str], str]:
    request = urllib.request.Request(url, headers={"Cookie": cookie})
    with urllib.request.urlopen(request, timeout=3) as response:
        headers = {key.lower(): value for key, value in response.headers.items()}
        lines = []
        while len(lines) < 12:
            line = response.readline().decode()
            lines.append(line)
            if line.startswith("data: ") and any(item.startswith("event: snapshot") for item in lines):
                break
        body = "".join(lines)
    return headers, body

def _wait_health(url: str, process: subprocess.Popen[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"haos-edge exited early with status {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=0.25) as response:
                return json.loads(response.read())
        except (OSError, urllib.error.URLError):
            time.sleep(0.05)
    raise AssertionError("haos-edge health endpoint did not become ready")


def _db_digest(path: Path) -> dict[str, str | None]:
    def digest(p: Path) -> str | None:
        return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None

    return {name: digest(Path(f"{path}{name}")) for name in ("", "-wal", "-shm")}


def _fixture_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE sessions (
                id TEXT, title TEXT, source TEXT, started_at REAL,
                last_activity_at REAL, message_count INTEGER, archived INTEGER,
                hidden INTEGER, pinned INTEGER, parent_session_id TEXT,
                profile_name TEXT, schema_version INTEGER, model TEXT, cwd TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("alpha-same", "A", "cli", 1, 2, 1, 0, 0, 0, None, "alpha", 1, "m", "/a"),
                ("beta-same", "B", "cli", 3, 4, 1, 0, 0, 0, None, "beta", 1, "m", "/b"),
            ],
        )
        conn.execute("PRAGMA journal_mode=WAL")


def test_contract_version_is_explicit_and_profile_is_required(tmp_path: Path):
    """The real observer process binds profile and versions its health envelope."""
    binary = Path(__file__).resolve().parents[2] / "packages/haos-edge/target/debug/haos-edge"
    if not binary.exists():
        binary = Path(__file__).resolve().parents[2] / "target/debug/haos-edge"
    assert binary.exists(), "build haos-edge before running its black-box contract tests"
    profile = "observer-contract-test"
    data_dir = tmp_path / "must-not-be-created"
    port = _free_port()
    process = subprocess.Popen(
        [
            str(binary), "server", "--host", "127.0.0.1", "--port", str(port),
            "--profile", profile, "--data-dir", str(data_dir), "--observer-only",
            "--writer-mode", "python",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "HAOS_HOME": str(tmp_path / "home")},
    )
    try:
        payload = _wait_health(f"http://127.0.0.1:{port}/health", process)
        assert payload["contract_version"] == CONTRACT_VERSION
        assert payload["profile"] == profile
        assert payload["schema_version"] == 1
        assert payload["data"]["status"] == "healthy"
        assert not data_dir.exists(), "observer startup must not create its data directory"
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def test_sessions_fast_requires_auth_before_reading_profile_db(tmp_path: Path):
    """Anonymous reads are rejected and leave a real profile DB untouched."""
    binary = Path(__file__).resolve().parents[2] / "target/debug/haos-edge"
    assert binary.exists(), "build haos-edge before running its black-box contract tests"
    data_dir = tmp_path / "profile"
    db = data_dir / "state.db"
    _fixture_db(db)
    before = _db_digest(db)
    port = _free_port()
    process = subprocess.Popen(
        [
            str(binary), "server", "--host", "127.0.0.1", "--port", str(port),
            "--profile", "auth-before-read", "--data-dir", str(data_dir),
            "--observer-only", "--writer-mode", "python",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "HAOS_HOME": str(tmp_path / "home")},
    )
    try:
        _wait_health(f"http://127.0.0.1:{port}/health", process)
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/sessions/fast")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=2)
        assert exc.value.code == 401
        assert _db_digest(db) == before
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _get_json(url: str, cookie: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Cookie": cookie})
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read())


def test_profile_a_b_a_uses_distinct_real_homes(tmp_path: Path):
    """Real observer processes must keep same-ID rows isolated across A/B/A."""
    binary = Path(__file__).resolve().parents[2] / "target/debug/haos-edge"
    assert binary.exists(), "build haos-edge before running its black-box contract tests"
    servers = []
    results = []
    try:
        for profile, title in (("alpha", "alpha-only"), ("beta", "beta-only")):
            data_dir = tmp_path / profile
            db = data_dir / "state.db"
            data_dir.mkdir()
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE sessions (id TEXT, title TEXT, started_at REAL, last_activity_at REAL)")
                conn.execute("INSERT INTO sessions VALUES ('same-id', ?, 1, 2)", (title,))
            cookie = _authenticated_cookie(data_dir)
            process, port = _start_observer(binary, tmp_path, profile, data_dir)
            servers.append(process)
            results.append((port, cookie))
        a1 = _get_json(f"http://127.0.0.1:{results[0][0]}/api/sessions/fast", results[0][1])
        b = _get_json(f"http://127.0.0.1:{results[1][0]}/api/sessions/fast", results[1][1])
        a2 = _get_json(f"http://127.0.0.1:{results[0][0]}/api/sessions/fast", results[0][1])
        for payload, profile, title in ((a1, "alpha", "alpha-only"), (b, "beta", "beta-only"), (a2, "alpha", "alpha-only")):
            assert payload["profile"] == profile
            assert [row["title"] for row in payload["data"]["sessions"]] == [title]
    finally:
        for process in servers:
            _stop_process(process)


def test_read_only_wal_does_not_change_database_or_sidecars(tmp_path: Path):
    """Observer reads a real WAL-mode DB without changing DB/WAL/SHM bytes."""
    binary = Path(__file__).resolve().parents[2] / "target/debug/haos-edge"
    assert binary.exists(), "build haos-edge before running its black-box contract tests"
    data_dir = tmp_path / "wal-profile"
    db = data_dir / "state.db"
    _fixture_db(db)
    cookie = _authenticated_cookie(data_dir)
    before = _db_digest(db)
    process, port = _start_observer(binary, tmp_path, "wal-contract", data_dir)
    try:
        payload = _get_json(f"http://127.0.0.1:{port}/api/sessions/fast", cookie)
        assert payload["data"]["count"] == 2
        assert _db_digest(db) == before
    finally:
        _stop_process(process)


def test_sse_contract_headers_and_envelope(tmp_path: Path):
    """A real authenticated stream has stable headers and a versioned snapshot."""
    binary = Path(__file__).resolve().parents[2] / "target/debug/haos-edge"
    assert binary.exists(), "build haos-edge before running its black-box contract tests"
    data_dir = tmp_path / "sse-profile"
    data_dir.mkdir()
    cookie = _authenticated_cookie(data_dir)
    process, port = _start_observer(binary, tmp_path, "sse-contract", data_dir)
    try:
        headers, body = _get_sse(f"http://127.0.0.1:{port}/api/events/stream", cookie)
        assert headers["content-type"].startswith("text/event-stream")
        assert "no-cache" in headers["cache-control"]
        assert "event: snapshot" in body
        payload_line = next(line[6:] for line in body.splitlines() if line.startswith("data: "))
        payload = json.loads(payload_line)
        assert payload["contract_version"] == CONTRACT_VERSION
        assert payload["profile"] == "sse-contract"
        assert payload["schema_version"] == 1
        assert payload["data"]["type"] == "snapshot"
    finally:
        _stop_process(process)


def _python_reference_sessions(candidate_dbs: list[Path]) -> list[dict[str, Any]]:
    """Faithful transcription of ``standalone.py::_sessions_list`` (fallback path).

    The candidate list is injected instead of the reference's hardcoded host paths so the
    fixture controls which databases participate; everything else (path dedupe, per-DB
    ``LIMIT 30``, ID dedupe across DBs, ordering, title derivation, timestamp formatting,
    field set and the final ``[:40]`` slice) mirrors the reference line for line.
    """
    candidate: list[Path] = []
    seen_paths: set[str] = set()
    for path in candidate_dbs:
        resolved = str(path.resolve()) if path.exists() else str(path)
        if resolved not in seen_paths and path.exists():
            seen_paths.add(resolved)
            candidate.append(path)

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for db_file in candidate:
        try:
            conn = sqlite3.connect(str(db_file), timeout=2.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, started_at, last_activity_at
                FROM sessions
                ORDER BY COALESCE(last_activity_at, started_at) DESC
                LIMIT 30
                """
            )
            for row in cursor.fetchall():
                sid = row["id"]
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)
                started_ts = row["started_at"] or 0
                updated_ts = row["last_activity_at"] or started_ts
                fmt = "%Y-%m-%d %H:%M:%S"
                started_str = datetime.datetime.fromtimestamp(started_ts).strftime(fmt) if started_ts else ""
                updated_str = datetime.datetime.fromtimestamp(updated_ts).strftime(fmt) if updated_ts else ""
                title = row["title"]
                if not title or not str(title).strip():
                    message = cursor.execute(
                        "SELECT content FROM messages WHERE session_id = ? AND role = 'user' "
                        "ORDER BY id ASC LIMIT 1",
                        (sid,),
                    ).fetchone()
                    if message and message["content"]:
                        first = str(message["content"]).strip()
                        title = first[:80] + ("…" if len(first) > 80 else "")
                results.append({
                    "session_id": sid,
                    "title": title or f"Session {sid}",
                    "started_at": started_str,
                    "updated_at": updated_str,
                    "updated_ts": updated_ts,
                    "db": str(db_file),
                })
            conn.close()
        except Exception:
            continue
    results.sort(key=lambda item: item.get("updated_ts", 0), reverse=True)
    return results[:40]


def _parity_db(path: Path, rows: list[tuple[Any, ...]], messages: list[tuple[str, int, str]] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE sessions (
                id TEXT, title TEXT, source TEXT, started_at REAL,
                last_activity_at REAL, message_count INTEGER, archived INTEGER,
                hidden INTEGER, pinned INTEGER, parent_session_id TEXT,
                profile_name TEXT, schema_version INTEGER, model TEXT, cwd TEXT
            )"""
        )
        conn.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
        conn.executemany(
            "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, 'user', ?)",
            [(mid, session_id, content) for session_id, mid, content in (messages or [])],
        )


def test_session_parity_fixture_is_required(tmp_path: Path):
    """Differentially compare every returned session field to the canonical Python fallback.

    Runs the real observer against a two-database fixture (shared IDs, empty titles that
    need first-user-message derivation, unsorted timestamps) and asserts the HTTP payload
    matches the reference transcription field for field.
    """
    binary = Path(__file__).resolve().parents[2] / "target/debug/haos-edge"
    assert binary.exists(), "build haos-edge before running its black-box contract tests"
    data_dir = tmp_path / "parity"
    data_dir.mkdir()
    primary = data_dir / "state.db"
    secondary = data_dir / "secondary.db"

    # Duplicate ID across DBs; the reference keeps the first DB's row.
    _parity_db(primary, [
        ("dup", None, "cli", 10.0, 20.0, 1, 0, 0, 0, None, "parity", 1, "m", "/a"),
        ("primary", "  Primary  ", "cli", 30.0, None, 1, 0, 0, 0, None, "parity", 1, "m", "/b"),
        ("no-title", "", "cli", None, 40.0, 1, 0, 0, 0, None, "parity", 1, "m", "/c"),
    ], [("no-title", 1, "  " + "x" * 100 + "  ")])
    _parity_db(secondary, [
        ("dup", "secondary wins if not deduped", "cli", 50.0, 60.0, 1, 0, 0, 0, None, "other", 1, "m", "/d"),
        ("secondary", None, "cli", 70.0, 80.0, 1, 0, 0, 0, None, "other", 1, "m", "/e"),
    ], [("secondary", 1, "secondary prompt")])

    cookie = _authenticated_cookie(data_dir)
    expected = _python_reference_sessions([primary, secondary])
    process, port = _start_observer(binary, tmp_path, "parity", data_dir,
                                    ["--sessions-db", str(secondary)])
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/sessions/fast?limit=30", headers={"Cookie": cookie}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            actual = json.load(response)
    finally:
        _stop_process(process)

    assert actual["contract_version"] == CONTRACT_VERSION
    assert actual["profile"] == "parity"
    assert actual["schema_version"] == 1
    observed = actual["data"]["sessions"]
    assert actual["data"]["count"] == len(observed), "count must equal the number of returned sessions"

    def _label(row: dict[str, Any]) -> Any:
        return row.get("session_id", row.get("id", "<no id field>"))

    assert observed == expected, (
        "sessions_fast diverges from the canonical fallback:\n"
        f"  missing: {[_label(row) for row in expected if row not in observed]}\n"
        f"  extra:   {[_label(row) for row in observed if row not in expected]}\n"
        f"  expected fields: {sorted({key for row in expected for key in row})}\n"
        f"  observed fields: {sorted({key for row in observed for key in row})}\n"
        f"  expected: {expected}\n"
        f"  observed: {observed}"
    )


def test_no_second_writer_surface_is_required(tmp_path: Path):
    """Observer exposes no ingest route and cannot mutate the event DB via HTTP."""
    binary = Path(__file__).resolve().parents[2] / "target/debug/haos-edge"
    assert binary.exists(), "build haos-edge before running its black-box contract tests"
    data_dir = tmp_path / "no-writer-profile"
    data_dir.mkdir()
    event_db = data_dir / "events.db"
    with sqlite3.connect(event_db) as conn:
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO events (payload) VALUES ('sentinel')")
    cookie = _authenticated_cookie(data_dir)
    before = _db_digest(event_db)
    process, port = _start_observer(binary, tmp_path, "no-writer", data_dir)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/events/ingest",
            data=b'{"event":"must-not-write"}',
            headers={"Cookie": cookie, "Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=3)
        assert exc.value.code == 404
        assert _db_digest(event_db) == before
    finally:
        _stop_process(process)
