"""Canonical ingestion is tail-first and lossless under bounded backpressure."""
import json
import sqlite3
import pytest

from hermes.platform.agent_framework.autonomy_config import AutonomyConfig
from hermes.platform.agent_framework.autonomy_sources import AutonomySources
from hermes.platform.agent_framework.event_queue import EventQueue


def database(home):
    db = sqlite3.connect(home / "events.db")
    db.execute("CREATE TABLE events(seq INTEGER,event_id TEXT,name TEXT,timestamp REAL,payload TEXT,trust_level TEXT)")
    return db


def append(db, seq, name="work.failed"):
    db.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (seq, f"event_{seq}", name, 1, json.dumps({"issue_id": str(seq)}), "untrusted"))
    db.commit()


def test_absence_does_not_create_source_or_cursor(tmp_path):
    queue = EventQueue(tmp_path / "agent")
    try:
        assert AutonomySources(tmp_path / "agent").ingest(queue, AutonomyConfig())["read"] == 0
        assert not (tmp_path / "events.db").exists()
        assert queue.get_cursor("events.db") is None
    finally:
        queue.close()



def test_oversized_payload_is_counted_and_reset_rejected(tmp_path):
    db = database(tmp_path)
    queue = EventQueue(tmp_path / "agent")
    source = AutonomySources(tmp_path / "agent")
    try:
        source.ingest(queue, AutonomyConfig())
        db.execute("INSERT INTO events VALUES(1,'large','work.failed',1,?,'untrusted')", (json.dumps({"data": "x" * 40000}),))
        db.commit()
        assert source.ingest(queue, AutonomyConfig())["invalid"] == 1
        assert queue.get_cursor("events.db") == 1
        db.execute("DELETE FROM events")
        db.commit()
        with pytest.raises(ValueError, match="regressed"):
            source.ingest(queue, AutonomyConfig())
    finally:
        db.close()
        queue.close()

def test_tail_first_order_full_resume_and_internal_loop_filter(tmp_path):
    db = database(tmp_path)
    append(db, 1)
    queue = EventQueue(tmp_path / "agent", max_pending=1)
    source = AutonomySources(tmp_path / "agent")
    config = AutonomyConfig(event_patterns=("*",))
    try:
        source.ingest(queue, config)
        assert queue.get_cursor("events.db") == 1
        append(db, 2)
        append(db, 3)
        append(db, 4, "pipeline.cycle.failed")
        assert source.ingest(queue, config)["accepted"] == 1
        assert queue.get_cursor("events.db") == 2
        first = queue.claim("test")
        assert first["event"]["id"] == "event_2"
        queue.finish(first["job_id"], first["owner"], "completed")
        assert source.ingest(queue, config)["read"] == 2
        second = queue.claim("test")
        assert second["event"]["id"] == "event_3"
        assert queue.get_cursor("events.db") == 4
    finally:
        db.close()
        queue.close()
