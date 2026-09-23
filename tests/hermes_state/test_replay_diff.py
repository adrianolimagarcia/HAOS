"""Behavior contracts for the Python/Rust golden replay boundary."""

import sqlite3

import pytest

from hermes_state_replay import (
    OperationSpec,
    ReplayDivergenceError,
    TableSpec,
    canonical_json,
    capture_observation,
    diff_observations,
)


@pytest.fixture
def fixture_db(tmp_path):
    """A disposable state-shaped fixture; never open the user's state.db."""
    path = tmp_path / "state.fixture.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, archived INTEGER);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT);
        INSERT INTO sessions VALUES ('s-2', 'Second', 0), ('s-1', 'First', 1);
        INSERT INTO messages VALUES (2, 's-1', 'assistant', 'ok'), (1, 's-1', 'user', 'hello');
        """
    )
    yield connection
    connection.close()


def _operation():
    return OperationSpec(
        "list_session_messages",
        {"session_id": "s-1", "include_archived": True},
        (
            TableSpec("sessions", ("id", "title", "archived"), ("id",)),
            TableSpec("messages", ("id", "session_id", "role", "content"), ("id",)),
        ),
    )


def test_canonical_json_is_stable_for_object_and_row_order():
    left = {"rows": [{"id": 2}, {"id": 1}], "request": {"b": 2, "a": 1}}
    right = {"request": {"a": 1, "b": 2}, "rows": [{"id": 2}, {"id": 1}]}
    assert canonical_json(left) == canonical_json(right)


def test_python_fixture_roundtrips_as_future_rust_json(fixture_db):
    operation = _operation()
    python_observation = capture_observation(fixture_db, operation, {"count": 2, "ok": True})
    rust_observation = type(python_observation).from_json(python_observation.canonical_json())
    diff = diff_observations(python_observation, rust_observation)
    assert diff.equal
    assert rust_observation.tables[0].rows[0]["id"] == 1


def test_diff_reports_keyed_row_divergence_deterministically(fixture_db):
    operation = _operation()
    expected = capture_observation(fixture_db, operation, {"count": 2})
    fixture_db.execute("UPDATE messages SET content = 'different' WHERE id = 2")
    fixture_db.commit()
    actual = capture_observation(fixture_db, operation, {"count": 2})

    diff = diff_observations(expected, actual)
    assert not diff.equal
    assert [item["kind"] for item in diff.differences] == ["row_changed"]
    assert diff.differences[0]["path"].startswith("/tables/messages/rows/")
    with pytest.raises(ReplayDivergenceError):
        diff.raise_if_divergent()


def test_only_declared_affected_tables_are_compared(fixture_db):
    operation = OperationSpec("read_session", {"id": "s-1"}, (TableSpec("sessions", ("id", "title"), ("id",)),))
    expected = capture_observation(fixture_db, operation, {"id": "s-1"})
    fixture_db.execute("UPDATE messages SET content = 'unobserved' WHERE id = 1")
    fixture_db.commit()
    actual = capture_observation(fixture_db, operation, {"id": "s-1"})
    assert diff_observations(expected, actual).equal
