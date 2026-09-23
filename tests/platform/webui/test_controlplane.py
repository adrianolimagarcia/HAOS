"""Control-plane overview and live-board contract tests."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.webui.controlplane import ControlPlaneService


class TestLiveBoardAggregation(unittest.TestCase):
    """Overview follows the live Kanban board without a graph projection."""

    def _make_kanban_db(self, tmp_path, rows):
        db = tmp_path / "kanban.db"
        con = sqlite3.connect(str(db))
        con.executescript("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, title TEXT, body TEXT, status TEXT,
                assignee TEXT, created_at REAL, started_at REAL, completed_at REAL
            );
        """)
        con.executemany(
            "INSERT INTO tasks (id, title, body, status, assignee) VALUES (?,?,?,?,?)",
            [(r["id"], r["title"], "", r["status"], r.get("assignee", "")) for r in rows],
        )
        con.commit()
        con.close()
        return db

    def test_overview_falls_back_to_canonical_live_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._make_kanban_db(Path(tmp), [
                {"id": "T-LIVE-1", "title": "Done live", "status": "done"},
                {"id": "T-LIVE-2", "title": "Failed live", "status": "failed"},
            ])
            event_store = EventStore(db_path=":memory:")
            with patch.dict(os.environ, {"HERMES_KANBAN_DB": str(db)}, clear=False):
                overview = ControlPlaneService(event_store=event_store).get_overview()
            self.assertEqual(overview.total_missions, 2)
            self.assertEqual(overview.board_db, str(db))
            self.assertGreaterEqual(overview.pool_size, 1)

    def test_overview_union_keeps_history_and_grows_with_live_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._make_kanban_db(Path(tmp), [
                {"id": "T-NEW-1", "title": "Just finished", "status": "done"},
            ])
            event_store = EventStore(db_path=":memory:")
            event_store.append(Event(name="haos.task.spawned", payload={"task_id": "T-OLD-1"}))
            with patch.dict(os.environ, {"HERMES_KANBAN_DB": str(db)}, clear=False):
                overview = ControlPlaneService(event_store=event_store).get_overview()
            self.assertEqual(overview.total_missions, 2)

    def test_pool_size_reflects_concurrency_guard(self):
        class FakeGuard:
            max_global = 6
        overview = ControlPlaneService(EventStore(db_path=":memory:"), concurrency_guard=FakeGuard()).get_overview()
        self.assertEqual(overview.pool_size, 6)


if __name__ == "__main__":
    unittest.main()
