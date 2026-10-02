"""Real SQLite/profile contracts for durable autonomy admission and fencing."""
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes.platform.agent_framework.event_queue import EventQueue
from hermes.platform.agent_framework.models import HAOSEvent


class Clock:
    def __init__(self):
        self.now = 10000.0

    def __call__(self):
        return self.now


def event(issue="disk", **kwargs):
    return HAOSEvent("health.issue", "monitor", payload={"issue": issue}, **kwargs)


def test_profile_a_b_a_persistence(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("HERMES_HOME", str(a))
    qa = EventQueue(a)
    accepted = qa.submit(event())
    qa.set_pause(True)
    qa.set_cursor("canonical", 7)
    qa.close()
    monkeypatch.setenv("HERMES_HOME", str(b))
    qb = EventQueue(b)
    assert qb.status()["counts"] == {}
    assert not qb.get_pause()
    assert qb.get_cursor("canonical") is None
    qb.close()
    monkeypatch.setenv("HERMES_HOME", str(a))
    qa = EventQueue(a)
    assert qa.get_pause() and qa.get_cursor("canonical") == 7
    assert qa.recent()[0]["job_id"] == accepted["job_id"]
    assert qa.db_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        qa.set_cursor("canonical", 6)
    qa.close()


def test_two_connections_atomic_dedup_and_claim(tmp_path):
    a, b = EventQueue(tmp_path), EventQueue(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda q: q.submit(event()), [a, b]))
    assert sum(r["accepted"] for r in results) == 1
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda q: q.claim("worker"), [a, b]))
    assert sum(c is not None for c in claims) == 1
    assert a.status()["budget_used"] == 1
    for q in (a, b):
        q.close()


def test_leases_fence_stale_even_same_worker_and_limit_recovery(tmp_path):
    clock = Clock()
    q = EventQueue(tmp_path, clock=clock)
    q.submit(event())
    first = q.claim("worker", 5)
    assert not first["recovered"]
    assert not q.finish(first["job_id"], "worker", "completed")
    clock.now += 4
    assert q.renew(first["job_id"], first["owner"], 5)
    clock.now += 6
    assert not q.renew(first["job_id"], first["owner"])
    assert not q.finish(first["job_id"], first["owner"], "completed")
    second = q.claim("worker", 5)
    assert second["recovered"] and second["attempts"] == 2
    assert second["owner"] != first["owner"]
    assert not q.finish(first["job_id"], first["owner"], "completed")
    clock.now += 6
    assert q.claim("worker") is None
    assert q.recent()[0]["status"] == "uncertain"
    q.close()


def test_pause_budget_backpressure_and_sliding_cooldown(tmp_path):
    clock = Clock()
    q = EventQueue(tmp_path, clock=clock, max_pending=1, cooldown_seconds=10)
    one = event()
    assert q.submit(one)["accepted"]
    two = event("memory")
    assert q.submit(two)["reason"] == "full"
    q.set_pause(True)
    assert q.claim("worker") is None
    q.close()
    q = EventQueue(tmp_path, clock=clock, max_pending=1, cooldown_seconds=10)
    assert q.get_pause()
    q.set_pause(False)
    claimed = q.claim("worker", max_jobs_per_hour=1)
    assert q.finish(claimed["job_id"], claimed["owner"], "completed", {"receipt": "ok"})
    assert q.submit(one)["reason"] == "duplicate_id"
    clock.now += 9
    repeat = event()
    assert q.submit(repeat)["reason"] == "cooldown"
    clock.now += 9
    assert q.submit(event())["reason"] == "cooldown"
    assert q.submit(two)["accepted"]  # full never consumes ID
    assert q.claim("worker", max_jobs_per_hour=1) is None
    clock.now += 3600
    assert q.claim("worker", max_jobs_per_hour=1)
    assert not q.reserve_budget(1)
    assert q.status()["budget_used"] == 1
    q.close()


@pytest.mark.parametrize("kwargs", [
    {"event_type": "pipeline.done"}, {"event_type": "autonomy.done"},
    {"source": "autonomy_service"}, {"metadata": {"origin": "autonomy_service"}},
    {"payload": {"origin": "autonomy_service"}},
])
def test_loop_filter(tmp_path, kwargs):
    q = EventQueue(tmp_path)
    values = dict(event_type="health.issue", source="monitor")
    values.update(kwargs)
    assert q.submit(HAOSEvent(**values))["reason"] == "filtered"
    assert not q.recent()
    q.close()


@pytest.mark.parametrize("kwargs", [
    {"id": "../bad"}, {"event_type": "has space"}, {"timestamp": math.nan},
    {"payload": {"bad": math.inf}}, {"payload": {"huge": "x" * 32769}},
])
def test_invalid_untrusted_events(tmp_path, kwargs):
    q = EventQueue(tmp_path)
    values = dict(event_type="health.issue", source="monitor")
    values.update(kwargs)
    assert q.submit(HAOSEvent(**values))["reason"] == "invalid"
    q.close()


@pytest.mark.parametrize("bad", [True, "5", -1, math.nan, math.inf])
def test_clock_fails_closed(tmp_path, bad):
    with pytest.raises(ValueError):
        EventQueue(tmp_path, clock=lambda: bad)
    assert not (tmp_path / "autonomy").exists()


def test_symlinks_rejected_before_connect_and_during_use(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        EventQueue(alias)
    with pytest.raises(ValueError):
        EventQueue(alias / ".." / "other")
    q = EventQueue(real)
    target = tmp_path / "outside"
    target.write_text("untouched")
    q.db_path.with_name("queue.db-journal").symlink_to(target)
    with pytest.raises(ValueError):
        q.submit(event())
    assert target.read_text() == "untouched"
    q.close()


def test_corrupt_existing_db_is_not_reset(tmp_path):
    directory = tmp_path / "autonomy"
    directory.mkdir()
    path = directory / "queue.db"
    path.write_bytes(b"corrupt not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        EventQueue(tmp_path)
    assert path.read_bytes() == b"corrupt not sqlite"


def test_named_missing_profile_not_created(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    profile = tmp_path / ".hermes" / "profiles" / "missing"
    monkeypatch.setenv("HERMES_HOME", str(profile))
    with pytest.raises(FileNotFoundError):
        EventQueue(profile)
    assert not profile.exists()


def test_readonly_snapshot_never_creates_or_changes_storage(tmp_path):
    base = tmp_path / "missing"
    assert not EventQueue.inspect(base)["available"]
    assert not base.exists()
    q = EventQueue(base)
    q.submit(event())
    q.set_pause(True)
    q.close()
    path = base / "autonomy" / "queue.db"
    before = path.read_bytes()
    snapshot = EventQueue.inspect(base)
    assert snapshot["available"] and snapshot["paused"]
    assert snapshot["counts"] == {"pending": 1}
    assert len(snapshot["recent_jobs"]) == 1
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_retention_bounded_and_pagination(tmp_path):
    clock = Clock()
    q = EventQueue(tmp_path, clock=clock, cooldown_seconds=0)
    first = event("0")
    for i in range(270):
        e = first if i == 0 else event(str(i))
        assert q.submit(e)["accepted"]
        job = q.claim("worker", max_jobs_per_hour=None)
        assert q.finish(job["job_id"], job["owner"], "completed")
        clock.now += 1
    assert q.status()["counts"]["completed"] == 256
    assert len(q.recent(100)) == 100
    assert len(q.recent(100, 200)) == 56
    assert q.submit(first)["reason"] == "duplicate_id"
    for i in range(1100):
        q.submit(event("same"))
    with q._tx() as db:
        assert db.execute("SELECT count(*) FROM seen").fetchone()[0] <= 1024
        assert db.execute("SELECT count(*) FROM fingerprints").fetchone()[0] <= 512
    with pytest.raises(ValueError):
        q.recent(101)
    q.close()
