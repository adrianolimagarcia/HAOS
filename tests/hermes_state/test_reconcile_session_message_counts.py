"""reconcile_session_message_counts: realign sessions.message_count with truth.

Canonical semantics (archive_and_compact docstring): ``message_count`` is the
session's ACTIVE row count. Incremental bumps only ADD, so inserts that later
vanished leave permanent inflation (observed: 8,392,043 vs 1,356 active rows).
The reconcile corrects only material divergence (> gap) and never touches
``tool_call_count`` (a SUM, not a row count) or zero-active sessions.
"""

from __future__ import annotations

import pytest

from hermes_state import SessionDB
from hermes_state_messages import _SESSION_MESSAGE_COUNT_GAP


@pytest.fixture
def db(tmp_path):
    database = SessionDB(db_path=tmp_path / "state.db")
    database.create_session("s1", source="cli")
    yield database
    database.close()


def _message_count(db, session="s1"):
    return db.get_session(session)["message_count"]


def _tool_call_count(db, session="s1"):
    return db.get_session(session)["tool_call_count"]


def _set_counter(db, value, session="s1"):
    db._write_rowcount(
        "UPDATE sessions SET message_count = ? WHERE id = ?", (value, session))


def test_reconcile_corrects_massively_inflated_counter(db):
    for index in range(10):
        db.append_message("s1", "user", content=f"u{index}")
    _set_counter(db, 8_392_043)

    assert db.reconcile_session_message_counts() == 1
    assert _message_count(db) == 10


def test_reconcile_fixes_undercount_beyond_gap(db):
    for index in range(_SESSION_MESSAGE_COUNT_GAP + 10):
        db.append_message("s1", "user", content=f"u{index}")
    _set_counter(db, 0)

    assert db.reconcile_session_message_counts() == 1
    assert _message_count(db) == _SESSION_MESSAGE_COUNT_GAP + 10


def test_small_drift_within_gap_is_left_alone(db):
    for index in range(60):
        db.append_message("s1", "user", content=f"u{index}")
    _set_counter(db, 60 + (_SESSION_MESSAGE_COUNT_GAP - 20))  # drift < gap

    assert db.reconcile_session_message_counts() == 0
    assert _message_count(db) == 60 + (_SESSION_MESSAGE_COUNT_GAP - 20)


def test_zero_active_session_is_untouched(db):
    for index in range(5):
        db.append_message("s1", "user", content=f"u{index}")
    db._write_rowcount("UPDATE messages SET active = 0 WHERE session_id = 's1'")
    before = _message_count(db)

    assert db.reconcile_session_message_counts() == 0
    assert _message_count(db) == before


def test_active_count_not_total_row_count(db):
    total = 2 * _SESSION_MESSAGE_COUNT_GAP + 20  # 120 when gap = 50
    for index in range(total):
        db.append_message("s1", "user", content=f"u{index}")
    db._write_rowcount(
        "UPDATE messages SET active = 0 WHERE session_id = 's1' "
        "AND id > (SELECT MIN(id) FROM messages WHERE session_id = 's1') "
        "AND id <= (SELECT MIN(id) FROM messages WHERE session_id = 's1') + ?",
        (total - _SESSION_MESSAGE_COUNT_GAP,),  # keep exactly gap rows active
    )
    active = _SESSION_MESSAGE_COUNT_GAP  # diff = 120-50 = 70 > gap → corrected

    assert db.reconcile_session_message_counts() == 1
    assert _message_count(db) == active  # NOT COUNT(*) of all rows


def test_tool_call_count_is_never_touched(db):
    for index in range(60):
        db.append_message(
            "s1", "user", content=f"u{index}",
            tool_calls=[{"id": f"c{index}", "function": {"name": "t", "arguments": "{}"}}],
        )
    before = _tool_call_count(db)
    _set_counter(db, 999_999)

    assert db.reconcile_session_message_counts() == 1
    assert _message_count(db) == 60
    assert _tool_call_count(db) == before


def test_one_pass_fixes_many_sessions_and_skips_healthy_ones(db):
    db.create_session("s2", source="cli")
    db.create_session("s3", source="cli")
    for index in range(60):
        db.append_message("s1", "user", content=f"a{index}")   # corrupted below
        db.append_message("s2", "user", content=f"b{index}")   # healthy
        db.append_message("s3", "user", content=f"c{index}")   # corrupted below
    _set_counter(db, 8_000_000)
    _set_counter(db, 8_000_000, session="s3")

    assert db.reconcile_session_message_counts() == 2
    assert _message_count(db) == 60
    assert _message_count(db, "s2") == 60
    assert _message_count(db, "s3") == 60
