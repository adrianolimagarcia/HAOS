"""Force close/write interleavings against real temporary SQLite stores."""
import sqlite3
import threading
from contextvars import copy_context

from hermes_state import SessionDB
from hermes_constants import set_hermes_home_override, reset_hermes_home_override


def test_close_waits_for_transaction_commit(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("s", source="test")
    original_lock = db._lock
    contended, entered, release = (threading.Event() for _ in range(3))
    errors = []

    class ObservedLock:
        def __enter__(self):
            # Observe actual lock contention, not merely that close was scheduled.
            if not original_lock.acquire(blocking=False):
                contended.set()
                original_lock.acquire()
            return self

        def __exit__(self, *args):
            original_lock.release()

    db._lock = ObservedLock()

    def write(conn):
        conn.execute("UPDATE sessions SET title='committed' WHERE id='s'")
        entered.set()
        assert release.wait(10)

    def run(fn):
        try:
            fn()
        except BaseException as exc:
            errors.append(exc)

    writer = threading.Thread(target=run, args=(lambda: db._execute_write(write),))
    closer = threading.Thread(target=run, args=(db.close,))
    writer.start()
    try:
        assert entered.wait(10)
        closer.start()
        assert contended.wait(10), "close never attempted the held transaction lock"
        assert db._conn is not None
    finally:
        release.set()
        writer.join(10)
        if closer.ident is not None:
            closer.join(10)
        db._lock = original_lock
        db.close()
    assert not writer.is_alive() and not closer.is_alive()
    assert not errors
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("SELECT title FROM sessions WHERE id='s'").fetchone() == ("committed",)
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_late_flush_reopens_original_home_after_close(tmp_path):
    home_a, home_b = tmp_path / "a", tmp_path / "b"
    home_a.mkdir()
    home_b.mkdir()
    db = SessionDB(db_path=home_a / "state.db")
    db.create_session("s", source="test")
    ready, release = threading.Event(), threading.Event()
    errors = []
    scope_handle = set_hermes_home_override(str(home_b))
    def worker():
        try:
            ready.set()
            assert release.wait(10)
            db.append_message("s", "user", "late")
        except BaseException as exc:
            errors.append(exc)

    # Carry the foreign-home context into the worker, unlike a bare Thread.
    thread = threading.Thread(target=copy_context().run, args=(worker,))
    thread.start()
    try:
        assert ready.wait(10)
        db.close()
        assert db._conn is None
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert not errors
        assert db.db_path == (home_a / "state.db").resolve()
        assert not (home_b / "state.db").exists()
    finally:
        release.set()
        thread.join(10)
        db.close()
        reset_hermes_home_override(scope_handle)
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("SELECT content FROM messages WHERE session_id='s'").fetchall() == [("late",)]
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
