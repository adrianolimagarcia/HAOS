"""Profile-bound durable event admission and fenced work leases.

Events/results are untrusted JSON, never instructions. A recovered claim is NOT
permission to repeat mutations: the service must reconcile durable receipts first
(or finish ``uncertain``). Leases fence queue writes, not external side effects.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from hermes.platform.agent_framework.models import HAOSEvent
from hermes.platform.agent_framework.state_manager import HAOSStateManager
from hermes_constants import assert_named_profile_home_live, mkdir_under_hermes_home


class EventQueue:
    """All admission/claim/budget decisions serialize with BEGIN IMMEDIATE.

    ``claim`` returns a unique ``owner`` fencing token. Pass that token (not the
    worker label) to renew/finish. Its optional budget is reserved in the SAME
    transaction as the claim; do not additionally call reserve_budget. A separate
    reservation is for non-queue consumers only and is intentionally not refunded.
    """

    def __init__(self, base_dir, *, max_pending=128, cooldown_seconds=300,
                 max_attempts=2, clock=time.time):
        self.max_pending = self._integer(max_pending, 1, 1024)
        self.max_attempts = self._integer(max_attempts, 1, 16)
        self.cooldown_seconds = self._number(cooldown_seconds, minimum=0)
        if not callable(clock):
            raise ValueError("clock must be callable")
        self.clock = clock
        self._now()
        # Validate lexical components BEFORE resolving, otherwise links disappear.
        lexical = Path(base_dir).absolute()
        HAOSStateManager._check_path(lexical)
        self.base_dir = Path(os.path.abspath(os.fspath(lexical)))
        self.db_path = self.base_dir / "autonomy" / "queue.db"
        self._check_storage()
        mkdir_under_hermes_home(self.db_path.parent)
        self._check_storage()
        fd = os.open(self.db_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.db_path, timeout=5, isolation_level=None,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            if self._db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Corrupt autonomy queue")
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA synchronous=FULL")
            # Bound physical growth too; freelist pages are reused, no unbounded WAL.
            self._db.execute("PRAGMA max_page_count=32768")
            with self._tx() as db:
                db.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, "
                           "event_id TEXT UNIQUE, fingerprint TEXT, event TEXT, status TEXT, "
                           "created REAL, updated REAL, attempts INTEGER DEFAULT 0, "
                           "owner TEXT, lease_until REAL, result TEXT)")
                db.execute("CREATE TABLE IF NOT EXISTS seen (event_id TEXT PRIMARY KEY, updated REAL)")
                db.execute("CREATE TABLE IF NOT EXISTS fingerprints (key TEXT PRIMARY KEY, updated REAL)")
                db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
                db.execute("CREATE TABLE IF NOT EXISTS budget (at REAL)")
                db.execute("INSERT OR IGNORE INTO settings VALUES ('pause', 'false')")
        except Exception:
            self._db.close()
            raise

    @staticmethod
    def _integer(value, minimum, maximum):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"integer must be in [{minimum}, {maximum}]")
        return value

    @staticmethod
    def _number(value, *, minimum=0):
        if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
            raise ValueError("expected finite nonnegative number")
        return float(value)

    def _now(self):
        return self._number(self.clock())

    def _check_storage(self):
        assert_named_profile_home_live(self.base_dir)
        for path in (self.db_path, self.db_path.with_name("queue.db-journal"),
                     self.db_path.with_name("queue.db-wal"), self.db_path.with_name("queue.db-shm")):
            HAOSStateManager._check_path(path)

    @contextmanager
    def _tx(self):
        with self._lock:
            self._check_storage()
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    @staticmethod
    def _json(value):
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False)
        if len(rendered.encode("utf-8")) > 32768:
            raise ValueError("JSON exceeds 32KiB")
        return rendered

    @staticmethod
    def _name(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:/-]{0,127}", value):
            raise ValueError("invalid identifier")
        return value

    def _prune(self, db):
        db.execute("DELETE FROM jobs WHERE job_id IN (SELECT job_id FROM jobs "
                   "WHERE status NOT IN ('pending','running') ORDER BY updated DESC, rowid DESC LIMIT -1 OFFSET 256)")
        for table, column, maximum in (("seen", "event_id", 1024), ("fingerprints", "key", 512)):
            db.execute(f"DELETE FROM {table} WHERE {column} IN (SELECT {column} FROM {table} "
                       f"ORDER BY updated DESC, rowid DESC LIMIT -1 OFFSET {maximum})")

    def submit(self, event: HAOSEvent, *, dedup_key=None):
        """Reject loops/invalid input before storage. Cooldown slides on repeats."""
        rejected = lambda reason, job_id=None: dict(accepted=False, reason=reason, job_id=job_id)
        try:
            if not isinstance(event, HAOSEvent):
                raise ValueError("expected HAOSEvent")
            self._name(event.id)
            self._name(event.event_type)
            self._name(event.source)
            self._number(event.timestamp)
            if not isinstance(event.payload, dict) or not isinstance(event.metadata, dict):
                raise ValueError("payload/metadata must be mappings")
            if (event.event_type.startswith(("pipeline.", "autonomy.")) or
                    event.source == "autonomy_service" or
                    event.metadata.get("origin") == "autonomy_service" or
                    event.payload.get("origin") == "autonomy_service"):
                return rejected("filtered")
            rendered = self._json(event.to_dict())
            # Stable issue identity, never timestamp/id or volatile metrics.
            identity = (event.event_type, event.source,
                        event.payload.get("issue", event.payload.get("issue_id")))
            if dedup_key is not None:
                if not isinstance(dedup_key, str) or not 1 <= len(dedup_key) <= 512:
                    raise ValueError("invalid dedup_key")
                identity = dedup_key
            fingerprint = hashlib.sha256(self._json(identity).encode()).hexdigest()
        except (ValueError, TypeError, AttributeError, RecursionError):
            return rejected("invalid")
        now = self._now()
        with self._tx() as db:
            prior = db.execute("SELECT job_id FROM jobs WHERE event_id=?", (event.id,)).fetchone()
            if prior or db.execute("SELECT 1 FROM seen WHERE event_id=?", (event.id,)).fetchone():
                return rejected("duplicate_id", prior[0] if prior else None)
            active = db.execute("SELECT job_id FROM jobs WHERE fingerprint=? AND status IN ('pending','running')",
                                (fingerprint,)).fetchone()
            last = db.execute("SELECT updated FROM fingerprints WHERE key=?", (fingerprint,)).fetchone()
            if active or (last and now - last[0] < self.cooldown_seconds):
                db.execute("INSERT OR REPLACE INTO seen VALUES (?,?)", (event.id, now))
                db.execute("INSERT OR REPLACE INTO fingerprints VALUES (?,?)", (fingerprint, now))
                self._prune(db)
                return rejected("duplicate" if active else "cooldown", active[0] if active else None)
            count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('pending','running')").fetchone()[0]
            if count >= self.max_pending:
                return rejected("full")
            job_id = "job_" + uuid.uuid4().hex
            db.execute("INSERT INTO jobs (job_id,event_id,fingerprint,event,status,created,updated) "
                       "VALUES (?,?,?,?,'pending',?,?)", (job_id, event.id, fingerprint, rendered, now, now))
            db.execute("INSERT OR REPLACE INTO seen VALUES (?,?)", (event.id, now))
            db.execute("INSERT OR REPLACE INTO fingerprints VALUES (?,?)", (fingerprint, now))
            self._prune(db)
            return dict(accepted=True, reason="accepted", job_id=job_id)

    def _budget(self, db, now, maximum):
        self._integer(maximum, 1, 1000)
        db.execute("DELETE FROM budget WHERE at<=?", (now - 3600,))
        if db.execute("SELECT count(*) FROM budget").fetchone()[0] >= maximum:
            return False
        db.execute("INSERT INTO budget VALUES (?)", (now,))
        return True

    def reserve_budget(self, max_jobs_per_hour=12):
        now = self._now()
        with self._tx() as db:
            return self._budget(db, now, max_jobs_per_hour)

    @staticmethod
    def _job(row):
        job = dict(row)
        job["event"] = json.loads(job["event"])
        job["result"] = json.loads(job["result"]) if job["result"] is not None else None
        job["recovered"] = job["attempts"] > 1
        return job

    def claim(self, owner, lease_seconds=120, *, max_jobs_per_hour=12):
        self._name(owner)
        lease_seconds = self._number(lease_seconds, minimum=0.001)
        if max_jobs_per_hour is not None:
            self._integer(max_jobs_per_hour, 1, 1000)
        now = self._now()
        lease_until = self._number(now + lease_seconds)
        with self._tx() as db:
            if self._paused(db):
                return None
            db.execute("UPDATE jobs SET status='uncertain',owner=NULL,lease_until=NULL,updated=? "
                       "WHERE status='running' AND lease_until<=? AND attempts>=?",
                       (now, now, self.max_attempts))
            row = db.execute("SELECT * FROM jobs WHERE status='pending' OR "
                             "(status='running' AND lease_until<=? AND attempts<?) "
                             "ORDER BY created,rowid LIMIT 1", (now, self.max_attempts)).fetchone()
            self._prune(db)
            if row is None or (max_jobs_per_hour is not None and not self._budget(db, now, max_jobs_per_hour)):
                return None
            token = owner + ":" + uuid.uuid4().hex
            db.execute("UPDATE jobs SET status='running',attempts=attempts+1,owner=?,lease_until=?,updated=? "
                       "WHERE job_id=?", (token, lease_until, now, row["job_id"]))
            return self._job(db.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone())

    def renew(self, job_id, owner, lease_seconds=120):
        lease_seconds = self._number(lease_seconds, minimum=0.001)
        now = self._now()
        lease_until = self._number(now + lease_seconds)
        with self._tx() as db:
            return db.execute("UPDATE jobs SET lease_until=?,updated=? WHERE job_id=? AND owner=? "
                              "AND status='running' AND lease_until>?",
                              (lease_until, now, job_id, owner, now)).rowcount == 1

    def finish(self, job_id, owner, status, result=None):
        if status not in ("completed", "failed", "uncertain", "skipped"):
            raise ValueError("invalid terminal status")
        rendered = self._json(result)
        now = self._now()
        with self._tx() as db:
            changed = db.execute("UPDATE jobs SET status=?,result=?,updated=?,owner=NULL,lease_until=NULL "
                                 "WHERE job_id=? AND owner=? AND status='running' AND lease_until>?",
                                 (status, rendered, now, job_id, owner, now)).rowcount == 1
            self._prune(db)
            return changed

    @staticmethod
    def _paused(db):
        value = db.execute("SELECT value FROM settings WHERE key='pause'").fetchone()
        if value is None or value[0] not in ("true", "false"):
            raise sqlite3.DatabaseError("Invalid pause state")
        return value[0] == "true"

    def get_pause(self):
        with self._tx() as db:
            return self._paused(db)

    def set_pause(self, paused):
        if type(paused) is not bool:
            raise ValueError("pause must be bool")
        with self._tx() as db:
            db.execute("UPDATE settings SET value=? WHERE key='pause'", (json.dumps(paused),))
        return paused

    def get_cursor(self, name):
        name = self._name(name)
        with self._tx() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", ("cursor:" + name,)).fetchone()
            return self._integer(int(row[0]), 0, 2**63 - 1) if row else None

    def set_cursor(self, name, seq):
        name = self._name(name)
        seq = self._integer(seq, 0, 2**63 - 1)
        with self._tx() as db:
            key = "cursor:" + name
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row and int(row[0]) > seq:
                raise ValueError("cursor cannot go backwards")
            if not row and db.execute("SELECT count(*) FROM settings WHERE key LIKE 'cursor:%'").fetchone()[0] >= 16:
                raise ValueError("too many cursors")
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(seq)))

    def status(self):
        now = self._now()
        with self._tx() as db:
            counts = {row[0]: row[1] for row in db.execute("SELECT status,count(*) FROM jobs GROUP BY status")}
            return {"paused": self._paused(db), "counts": counts, "max_pending": self.max_pending,
                    "budget_used": db.execute("SELECT count(*) FROM budget WHERE at>?", (now - 3600,)).fetchone()[0]}

    def recent(self, limit=50, offset=0):
        limit = self._integer(limit, 1, 100)
        offset = self._integer(offset, 0, 10000)
        with self._tx() as db:
            return [self._job(row) for row in db.execute(
                "SELECT * FROM jobs ORDER BY created DESC,rowid DESC LIMIT ? OFFSET ?", (limit, offset))]

    @classmethod
    def inspect(cls, base_dir, limit=20):
        """Dashboard snapshot: no creation, migration, cleanup or write connection."""
        cls._integer(limit, 1, 100)
        base = Path(base_dir).absolute()
        HAOSStateManager._check_path(base)
        path = base / "autonomy" / "queue.db"
        for candidate in (path, path.with_name("queue.db-journal"),
                          path.with_name("queue.db-wal"), path.with_name("queue.db-shm")):
            HAOSStateManager._check_path(candidate)
        snapshot = {"available": False, "paused": False, "counts": {},
                    "budget_used": 0, "recent_jobs": []}
        if not path.exists():
            return snapshot
        assert_named_profile_home_live(base)
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN")
            snapshot.update(available=True, paused=cls._paused(db),
                            counts={row[0]: row[1] for row in db.execute(
                                "SELECT status,count(*) FROM jobs GROUP BY status")},
                            budget_used=db.execute("SELECT count(*) FROM budget WHERE at>?",
                                                   (time.time() - 3600,)).fetchone()[0],
                            recent_jobs=[cls._job(row) for row in db.execute(
                                "SELECT * FROM jobs ORDER BY created DESC,rowid DESC LIMIT ?", (limit,))])
            return snapshot
        finally:
            db.close()

    def close(self):
        with self._lock:
            self._db.close()
