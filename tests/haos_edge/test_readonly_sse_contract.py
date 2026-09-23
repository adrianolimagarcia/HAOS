"""Acceptance tests for the blocked Rust read-only SSE slice.

These are contract tests, not source-shape checks. They use a real SQLite fixture and the
real profile resolver/reference store. The HTTP checks are marked integration because the
current crate does not expose an in-process router builder.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

CONTRACT_VERSION = "haos-edge.readonly-sse.v1"


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


def test_contract_version_is_explicit_and_profile_is_required():
    """The current API has neither a versioned envelope nor an explicit profile parameter."""
    assert CONTRACT_VERSION == "haos-edge.readonly-sse.v1"
    pytest.fail(
        "BLOCKED: current /api/sessions/fast and /api/events/stream responses do not carry "
        "contract_version/profile and the server derives data_dir only from HAOS_DATA_DIR"
    )


def test_sessions_fast_requires_auth_before_reading_profile_db():
    """Missing credentials must be rejected before any SQLite read."""
    pytest.fail("BLOCKED: current /api/sessions/fast returns 200 without auth")


def test_profile_a_b_a_uses_distinct_real_homes(tmp_path: Path):
    """The route must bind immutable A/B homes and never leak same-named rows."""
    alpha = tmp_path / "alpha" / "state.db"
    beta = tmp_path / "beta" / "state.db"
    # SQLite cannot have duplicate primary keys in one DB; use independent files to model
    # the collision across profiles.
    for db, profile in ((alpha, "alpha"), (beta, "beta")):
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE sessions (id TEXT, title TEXT, profile_name TEXT)")
            conn.execute("INSERT INTO sessions VALUES ('same','same',?)", (profile,))
    assert alpha.read_bytes() != beta.read_bytes()
    pytest.fail("BLOCKED: current Rust server does not accept/bind an explicit profile home")


def test_read_only_wal_does_not_change_database_or_sidecars(tmp_path: Path):
    """The eventual implementation must prove observer-only behavior by bytes."""
    db = tmp_path / "state.db"
    _fixture_db(db)
    before = _db_digest(db)
    # This is the reference-side invariant; Rust integration must run the same assertion.
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2) as conn:
        conn.execute("PRAGMA query_only=ON")
        assert conn.execute("SELECT count(*) FROM sessions").fetchone() == (2,)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE forbidden (x INTEGER)")
    assert _db_digest(db) == before


def test_sse_contract_headers_and_envelope():
    """SSE must expose a single stable header set and versioned data envelope."""
    pytest.fail(
        "BLOCKED: live E2E stream passed, but the current route has no explicit contract/profile "
        "envelope and the endpoint is coupled to the unscoped data_dir"
    )


def test_session_parity_fixture_is_required():
    """Archived/branch/order/fields/schema_version divergence is a release blocker."""
    pytest.fail(
        "BLOCKED: observed parity divergence in archived, parent/branch, ordering, fields, and "
        "schema_version; implement the reference filter/query contract before enabling Rust"
    )


def test_no_second_writer_surface_is_required():
    """No Rust endpoint may ingest or asynchronously flush events to SQLite."""
    pytest.fail(
        "BLOCKED: current haos-edge contains EventHub::spawn_writer_task, writable event ingest, "
        "and other writable SQLite paths; cannot prove Python is the sole writer"
    )
