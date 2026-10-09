"""Deterministic regression and concurrency tests for kanban create_task idempotency race.

Verifies:
1. Concurrent create_task calls with the same idempotency_key are strictly serialized
   under the write lock and return the exact same task ID (atomic de-duplication).
2. The partial UNIQUE index `idx_tasks_idempotency_unique` enforces uniqueness
   for active tasks (status != 'archived') while allowing key reuse once archived.
3. Multiple tasks with NULL idempotency_key coexist without collision.
4. Concurrent threads via threading.Barrier reproduce zero duplicates.
"""

import sqlite3
import threading
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


def test_concurrent_create_task_with_same_idempotency_key_is_atomic(tmp_path):
    """Multiple concurrent workers attempting to create a task with the same
    idempotency key must all receive the identical task_id and insert exactly 1 row."""
    db_path = tmp_path / "race_test.db"
    # Pre-initialize schema
    init_conn = kbc.connect(db_path)
    init_conn.close()

    concurrency = 10
    barrier = threading.Barrier(concurrency)
    results = []
    errors = []

    def worker(worker_num: int):
        conn = kbc.connect(db_path)
        try:
            barrier.wait()
            tid = kb.create_task(
                conn,
                title=f"Concurrent Task from worker {worker_num}",
                idempotency_key="concurrent_race_test_key",
                assignee="ops",
            )
            results.append(tid)
        except Exception as exc:
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Unexpected worker exceptions: {errors}"
    assert len(results) == concurrency
    # All workers must receive the exact same task ID
    assert len(set(results)) == 1, f"Expected 1 unique task id, got {set(results)}"

    # Database must contain exactly 1 row for this idempotency key
    verify_conn = kbc.connect(db_path)
    try:
        rows = verify_conn.execute(
            "SELECT id, title, idempotency_key, status FROM tasks WHERE idempotency_key = ?",
            ("concurrent_race_test_key",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == results[0]
        assert rows[0]["status"] in ("ready", "todo")
    finally:
        verify_conn.close()


def test_idempotency_key_can_be_reused_after_archived(tmp_path):
    """Archiving a task allows creating a new task with the same idempotency key."""
    db_path = tmp_path / "lifecycle_test.db"
    conn = kbc.connect(db_path)
    try:
        shared_key = "reusable_flow_key"

        # 1. Create first active task
        t1 = kb.create_task(conn, title="Initial task", idempotency_key=shared_key)
        assert t1 is not None

        # 2. Calling create_task again while active returns same task
        t1_again = kb.create_task(conn, title="Initial task duplicate call", idempotency_key=shared_key)
        assert t1_again == t1

        # 3. Archive the first task
        kb.archive_task(conn, t1)
        task1 = kb.get_task(conn, t1)
        assert task1 is not None
        assert task1.status == "archived"

        # 4. Creating a task with the same idempotency key now creates a brand new task
        t2 = kb.create_task(conn, title="Subsequent task", idempotency_key=shared_key)
        assert t2 != t1

        # 5. Check both tasks exist in the database
        rows = conn.execute(
            "SELECT id, status, idempotency_key FROM tasks WHERE idempotency_key = ? ORDER BY created_at ASC",
            (shared_key,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["id"] == t1
        assert rows[0]["status"] == "archived"
        assert rows[1]["id"] == t2
        assert rows[1]["status"] in ("ready", "todo")
    finally:
        conn.close()


def test_unique_partial_index_prevents_duplicate_active_inserts_at_sql_level(tmp_path):
    """idx_tasks_idempotency_unique strictly rejects raw duplicate active inserts
    at the database level, but permits duplicate keys if the prior row is archived."""
    db_path = tmp_path / "index_test.db"
    conn = kbc.connect(db_path)
    try:
        # Insert first active task
        conn.execute(
            "INSERT INTO tasks (id, title, status, idempotency_key, created_at) VALUES ('t_sql_1', 'SQL 1', 'ready', 'sql_dup_key', 100)"
        )
        conn.commit()

        # Second active insert with same key must trigger sqlite3.IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks (id, title, status, idempotency_key, created_at) VALUES ('t_sql_2', 'SQL 2', 'ready', 'sql_dup_key', 101)"
            )
            conn.commit()

        # Archive first task
        conn.execute("UPDATE tasks SET status = 'archived' WHERE id = 't_sql_1'")
        conn.commit()

        # Second insert must succeed now that previous task is archived
        conn.execute(
            "INSERT INTO tasks (id, title, status, idempotency_key, created_at) VALUES ('t_sql_2', 'SQL 2', 'ready', 'sql_dup_key', 102)"
        )
        conn.commit()

        # Third active insert with same key while t_sql_2 is ready must fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks (id, title, status, idempotency_key, created_at) VALUES ('t_sql_3', 'SQL 3', 'ready', 'sql_dup_key', 103)"
            )
            conn.commit()
    finally:
        conn.close()


def test_multiple_tasks_with_null_idempotency_key_coexist(tmp_path):
    """Tasks without an idempotency key (idempotency_key=None) must never conflict."""
    db_path = tmp_path / "null_key_test.db"
    conn = kbc.connect(db_path)
    try:
        t1 = kb.create_task(conn, title="Normal Task 1")
        t2 = kb.create_task(conn, title="Normal Task 2")
        t3 = kb.create_task(conn, title="Normal Task 3")

        assert len({t1, t2, t3}) == 3

        rows = conn.execute(
            "SELECT count(*) as cnt FROM tasks WHERE idempotency_key IS NULL"
        ).fetchone()
        assert rows["cnt"] == 3
    finally:
        conn.close()


def test_migration_fail_closed_on_legacy_duplicates_without_mutating_cards(tmp_path, monkeypatch):
    """REGRA OBRIGATORIA: Never auto-archive or auto-delete duplicate cards.
    
    If a legacy database contains multiple non-archived rows with the same idempotency key:
    1. Migration must NOT mutate or archive any historical cards.
    2. Conflict is detected and written to an auditable JSON report.
    3. In strict mode, KanbanIdempotencyConflictError is raised.
    4. Unique index is NOT created while conflicts remain unresolved.
    """
    db_path = tmp_path / "legacy_dups_fail_closed.db"
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.row_factory = sqlite3.Row
    raw_conn.executescript(kb.SCHEMA_SQL)

    # Insert two active tasks sharing the same idempotency key
    raw_conn.execute(
        "INSERT INTO tasks (id, title, status, created_at, idempotency_key) "
        "VALUES ('card_a', 'Card A', 'ready', 100, 'dup_key_omega')"
    )
    raw_conn.execute(
        "INSERT INTO tasks (id, title, status, created_at, idempotency_key) "
        "VALUES ('card_b', 'Card B', 'todo', 200, 'dup_key_omega')"
    )
    raw_conn.commit()
    raw_conn.close()

    # Enable strict migration flag to verify fail-closed exception
    monkeypatch.setenv("HERMES_KANBAN_STRICT_MIGRATION", "1")

    report_out = tmp_path / "conflict_report.json"
    with pytest.raises(kbc.KanbanIdempotencyConflictError) as exc_info:
        conn = kbc.connect(db_path)

    err = exc_info.value
    assert len(err.conflicts) == 1
    assert err.conflicts[0]["idempotency_key"] == "dup_key_omega"
    assert err.conflicts[0]["count"] == 2

    # Verify that NO historical cards were mutated or archived
    verify_conn = sqlite3.connect(str(db_path))
    verify_conn.row_factory = sqlite3.Row
    rows = verify_conn.execute(
        "SELECT id, status FROM tasks WHERE idempotency_key = 'dup_key_omega' ORDER BY id"
    ).fetchall()
    status_by_id = {r["id"]: r["status"] for r in rows}
    assert status_by_id["card_a"] == "ready"  # INTACT!
    assert status_by_id["card_b"] == "todo"   # INTACT!

    # Verify unique index was NOT created
    indexes = {
        r["name"]
        for r in verify_conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    }
    assert "idx_tasks_idempotency_unique" not in indexes
    verify_conn.close()


def test_resolve_idempotency_conflicts_approved_preserves_events(tmp_path):
    """Resolving proven duplicates requires explicit approval and archives losers via archive_task,
    recording full 'archived' events with zero loss of events and enabling idx_tasks_idempotency_unique."""
    db_path = tmp_path / "resolve_approved.db"
    conn = kbc.connect(db_path)
    try:
        # Create initial legacy-like state before unique index
        # Drop unique index temporarily to insert duplicates for testing resolution
        conn.execute("DROP INDEX IF EXISTS idx_tasks_idempotency_unique")
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at, idempotency_key) "
            "VALUES ('dup_1', 'Original Card', 'ready', 100, 'shared_key')"
        )
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at, idempotency_key) "
            "VALUES ('dup_2', 'Duplicate Card', 'ready', 200, 'shared_key')"
        )
        conn.commit()

        # Attempting mutation without approved=True must raise PermissionError
        with pytest.raises(PermissionError):
            kbc.resolve_idempotency_conflicts_approved(conn, {"shared_key": "dup_2"}, approved=False)

        # Resolving with approved=True keeps winner ('dup_2') and archives loser ('dup_1')
        result = kbc.resolve_idempotency_conflicts_approved(
            conn, {"shared_key": "dup_2"}, approved=True
        )
        assert result["resolved_keys"] == 1
        assert result["archived_cards"] == ["dup_1"]
        assert result["remaining_conflicts"] == 0

        # Verify winner status remains active
        winner = conn.execute("SELECT status FROM tasks WHERE id = 'dup_2'").fetchone()
        assert winner["status"] == "ready"

        # Verify loser was archived
        loser = conn.execute("SELECT status FROM tasks WHERE id = 'dup_1'").fetchone()
        assert loser["status"] == "archived"

        # Verify full event preservation in task_events
        events = conn.execute(
            "SELECT kind FROM task_events WHERE task_id = 'dup_1' ORDER BY id"
        ).fetchall()
        event_kinds = [e["kind"] for e in events]
        assert "archived" in event_kinds

        # Verify idx_tasks_idempotency_unique is now active and enforced
        indexes = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        }
        assert "idx_tasks_idempotency_unique" in indexes

        # Inserting a duplicate active task now fails closed via IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks (id, title, status, created_at, idempotency_key) "
                "VALUES ('dup_3', 'Illegal Dupe', 'ready', 300, 'shared_key')"
            )
            conn.commit()
    finally:
        conn.close()
