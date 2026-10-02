"""Read-only canonical event ingestion; queue admission owns deduplication."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import HAOSEvent
from .state_manager import HAOSStateManager


class AutonomySources:
    cursor_name = "events.db"

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir).absolute()
        HAOSStateManager._check_path(self.base_dir)
        self.path = self.base_dir.parent / "events.db"

    def ingest(self, queue, config):
        """Drain the earliest batch; a full queue must not acknowledge that row."""
        for suffix in ("", "-wal", "-shm", "-journal"):
            HAOSStateManager._check_path(self.path.with_name(self.path.name + suffix))
        if not self.path.is_file():
            return {"read": 0, "accepted": 0}
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            columns = {r[1] for r in db.execute("PRAGMA table_info(events)")}
            if not {"seq", "event_id", "name", "timestamp", "payload", "trust_level"} <= columns:
                raise ValueError("Unsupported event schema")
            maximum = db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            cursor = queue.get_cursor(self.cursor_name)
            if cursor is not None and maximum < cursor:
                raise ValueError("Event source cursor regressed")
            if cursor is None:
                queue.set_cursor(self.cursor_name, maximum)
                return {"read": 0, "accepted": 0}
            rows = db.execute("SELECT seq,event_id,name,timestamp,trust_level,substr(payload,1,32769) AS payload,length(CAST(payload AS BLOB)) AS payload_bytes FROM events WHERE seq>? ORDER BY seq ASC,timestamp ASC LIMIT 64", (cursor,)).fetchall()
        counts = {"read": 0, "accepted": 0, "filtered": 0, "invalid": 0}
        for row in rows:
            result = {"reason": "filtered", "accepted": False}
            if config.matches(row["name"]):
                try:
                    if row["payload_bytes"] > 32768:
                        raise ValueError("Payload oversized")
                    payload = json.loads(row["payload"])
                    if not isinstance(payload, dict):
                        raise ValueError("Invalid payload")
                    event = HAOSEvent(row["name"], "event_store", id=row["event_id"],
                                      timestamp=row["timestamp"], payload=payload,
                                      metadata={"trust_level": row["trust_level"]})
                    result = queue.submit(event)
                except (ValueError, TypeError, RecursionError):
                    result = {"reason": "invalid", "accepted": False}
            if result["reason"] == "full":
                break
            queue.set_cursor(self.cursor_name, row["seq"])
            counts["read"] += 1
            counts["accepted"] += int(result["accepted"])
            if result["reason"] in ("filtered", "invalid"):
                counts[result["reason"]] += 1
        return counts
