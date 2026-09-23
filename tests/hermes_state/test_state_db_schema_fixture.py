"""Deterministic contract for the versioned SessionDB DDL evidence.

The SQL fixtures are generated from the checked-in SessionDB DDL constants; this
exercise executes the versioned files and verifies their observable SQLite
objects rather than comparing source text.
"""

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DDL = ROOT / "docs" / "haos"


def _objects(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = ? AND name NOT LIKE 'sqlite_%' ORDER BY name",
            (kind,),
        )
    }


def _fixture_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript((DDL / "state-db-schema.sql").read_text(encoding="utf-8"))
    conn.executescript((DDL / "state-db-fts.sql").read_text(encoding="utf-8"))
    return conn


def test_versioned_schema_fixture_matches_runtime_ddl():
    """The fixture must be executable and retain the runtime's DDL objects."""
    from hermes_state_common import DEFERRED_INDEX_SQL, FTS_SQL, FTS_TRIGRAM_SQL, SCHEMA_SQL

    fixture = _fixture_connection()
    runtime = sqlite3.connect(":memory:")
    try:
        runtime.executescript(SCHEMA_SQL)
        runtime.executescript(DEFERRED_INDEX_SQL)
        runtime.executescript(FTS_SQL)
        runtime.executescript(FTS_TRIGRAM_SQL)
        for kind in ("table", "index", "trigger", "view"):
            assert _objects(fixture, kind) == _objects(runtime, kind)
    finally:
        fixture.close()
        runtime.close()


def test_fixture_captures_pragmas_and_trigger_behavior():
    """Foreign keys and display-order triggers are behaviorally testable."""
    conn = _fixture_connection()
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.execute(
            "INSERT INTO sessions(id, source, started_at) VALUES (?, ?, ?)",
            ("s", "fixture", 1.0),
        )
        conn.execute(
            "INSERT INTO messages(session_id, role, content, timestamp, display_identity) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s", "user", "hello", 1.0, b"same"),
        )
        display_order = conn.execute(
            "SELECT display_order FROM messages WHERE session_id = 's'"
        ).fetchone()[0]
        assert display_order is not None
        try:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, timestamp) "
                "VALUES (?, ?, ?, ?)",
                ("missing", "user", "bad", 2.0),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("foreign-key pragma did not reject an orphan message")
    finally:
        conn.close()
