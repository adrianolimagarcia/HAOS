"""Invariants for the canonical SQLite connect/transaction layer (``hermes_cli/sqlite_util.py``).

Every small store used to carry its own connect + PRAGMA + ``with conn:`` stack, so the #69567 fd-leak
fix and the WAL fallback rules had to be re-pasted per module (and at least one copy missed each).
These tests pin the contract: one opener, one closer, and every store routed through them.
"""

from __future__ import annotations

import sqlite3

import pytest

from hermes_cli import sqlite_util


def test_transaction_closes_the_connection_even_when_the_body_raises(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite_util.open_db(db, db_label="t.db", wal=False)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()

    with pytest.raises(RuntimeError):
        with sqlite_util.transaction(conn) as c:
            c.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("mid-transaction")

    # Rolled back AND closed: a closed connection refuses every statement.
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    with sqlite3.connect(db) as check:
        assert check.execute("SELECT count(*) FROM t").fetchone()[0] == 0

    # The success path closes too.
    conn = sqlite_util.open_db(db, db_label="t.db", wal=False)
    with sqlite_util.transaction(conn) as c:
        c.execute("INSERT INTO t VALUES (2)")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_open_db_closes_the_half_open_connection_when_initialize_raises(monkeypatch, tmp_path):
    opened = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite_util.sqlite3, "connect", tracking_connect)

    def broken(conn):
        raise sqlite3.OperationalError("boom")

    with pytest.raises(sqlite3.OperationalError):
        sqlite_util.open_db(tmp_path / "x.db", db_label="x.db", wal=False, initialize=broken)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


# Every store that opens its own SQLite file (path -> module attribute holding the opener).
_STORE_OPENERS = (
    ("agent.verification_evidence", "_connect"),
    ("cron.executions", "_connect"),
    ("cron.incidents", "_connect"),
    ("cron.notepad", "_connect"),
    ("cron.delivery_queue", "_connect"),
    ("gateway.delivery_ledger", "_connect"),
    ("tools.async_delegation", "_connect"),
    ("hermes_cli.projects_db", "connect"),
    ("gateway.hosted_rooms_common", "open_sqlite"),
)


@pytest.mark.parametrize("module_name, attr", _STORE_OPENERS)
def test_every_store_opens_through_the_canonical_open_db(monkeypatch, tmp_path, module_name, attr):
    import importlib

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    module = importlib.import_module(module_name)
    for name in ("EXECUTIONS_FILE", "NOTEPAD_FILE", "DELIVERY_DB"):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, tmp_path / f"{name.lower()}.db")
    if module_name == "cron.incidents":
        monkeypatch.setattr(module._executions, "EXECUTIONS_FILE", tmp_path / "executions.db")
    if module_name == "agent.verification_evidence":
        monkeypatch.setattr(module, "_db_path", lambda: tmp_path / "ve.db")
    if module_name in ("gateway.delivery_ledger", "tools.async_delegation"):
        monkeypatch.setattr(module, "_db_path", lambda: tmp_path / "state.db")

    calls = []
    real_open_db = sqlite_util.open_db

    def spy(path, **kwargs):
        calls.append(kwargs)
        return real_open_db(path, **kwargs)

    # Patch where production reads: a module-level ``from sqlite_util import open_db`` binds its own name.
    monkeypatch.setattr(sqlite_util, "open_db", spy)
    if getattr(module, "open_db", None) is real_open_db:
        monkeypatch.setattr(module, "open_db", spy)
    opener = getattr(module, attr)
    args = (tmp_path / "opened.db",) if module_name == "gateway.hosted_rooms_common" else ()
    if module_name == "hermes_cli.projects_db":
        args = (tmp_path / "projects.db",)
    conn = opener(*args)
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    finally:
        conn.close()
    assert len(calls) == 1 and calls[0]["db_label"], module_name


def test_plugin_db_wal_goes_through_the_shared_fallback(monkeypatch, tmp_path):
    """A raw ``PRAGMA journal_mode=WAL`` bypasses the network-FS fallback and the WAL-reset-bug gate;
    plugin databases must obey the same rules as every core store."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_state_wal
    from plugins import plugin_storage

    seen = []
    real = hermes_state_wal.apply_wal_with_fallback

    def spy(conn, **kwargs):
        seen.append(kwargs["db_label"])
        return real(conn, **kwargs)

    monkeypatch.setattr(hermes_state_wal, "apply_wal_with_fallback", spy)
    conn = plugin_storage.plugin_db("board")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
    assert seen == ["plugin-data/board/data.db"]


def test_write_txn_commits_on_success_and_rolls_back_on_error(tmp_path):
    db = tmp_path / "write_txn.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (val INT)")
    conn.commit()

    # Success case: commits and connection remains usable
    with sqlite_util.write_txn(conn) as c:
        c.execute("INSERT INTO t VALUES (10)")
    assert conn.execute("SELECT val FROM t").fetchall() == [(10,)]

    # Error case: rolls back and raises original error
    with pytest.raises(RuntimeError, match="abort write"):
        with sqlite_util.write_txn(conn) as c:
            c.execute("INSERT INTO t VALUES (20)")
            raise RuntimeError("abort write")
    assert conn.execute("SELECT val FROM t").fetchall() == [(10,)]

    # Connection remains usable for another write_txn
    with sqlite_util.write_txn(conn) as c:
        c.execute("INSERT INTO t VALUES (30)")
    assert conn.execute("SELECT val FROM t").fetchall() == [(10,), (30,)]
    conn.close()


def test_write_txn_rolls_back_if_commit_fails(tmp_path):
    class FailingCommitConnection:
        def __init__(self, real_conn):
            self._real = real_conn
            self.rolled_back = False

        def execute(self, sql, *args, **kwargs):
            cmd = str(sql).strip().upper()
            if cmd == "COMMIT":
                raise sqlite3.OperationalError("disk I/O error on commit")
            if cmd == "ROLLBACK":
                self.rolled_back = True
            return self._real.execute(sql, *args, **kwargs)

    db = tmp_path / "commit_fail.db"
    real_conn = sqlite3.connect(db)
    real_conn.execute("CREATE TABLE t (val INT)")
    real_conn.commit()

    wrapped = FailingCommitConnection(real_conn)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error on commit"):
        with sqlite_util.write_txn(wrapped) as c:  # type: ignore[arg-type]
            c.execute("INSERT INTO t VALUES (42)")

    assert wrapped.rolled_back is True
    real_conn.close()

