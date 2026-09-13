"""Baseline store for the HAOS Eval Harness (Emenda 20).

Ouroboros starts collecting baselines in P0; this tiny store keeps labeled
snapshots (suite + label + metrics JSON) so later phases can compare against
history instead of re-running everything.
"""

import json
import sqlite3
import time
from typing import Dict, Any, List, Optional


class BaselineStore:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn = None
        if self.db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:")
            self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _get_connection(self):
        if self._conn:
            return self._conn
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_connection()
        conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_baselines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suite_id TEXT NOT NULL,
            label TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """)
        conn.commit()
        if not self._conn:
            conn.close()

    def save(self, suite_id: str, label: str, metrics: Dict[str, Any]) -> int:
        conn = self._get_connection()
        cursor = conn.execute(
            "INSERT INTO eval_baselines (suite_id, label, metrics_json, created_at) VALUES (?, ?, ?, ?)",
            (suite_id, label, json.dumps(metrics), time.time()),
        )
        conn.commit()
        row_id = cursor.lastrowid
        if not self._conn:
            conn.close()
        return row_id

    def latest(self, suite_id: str, label: Optional[str] = None) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        if label:
            cursor = conn.execute(
                "SELECT * FROM eval_baselines WHERE suite_id = ? AND label = ? ORDER BY created_at DESC LIMIT 1",
                (suite_id, label),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM eval_baselines WHERE suite_id = ? ORDER BY created_at DESC LIMIT 1",
                (suite_id,),
            )
        row = cursor.fetchone()
        if not self._conn:
            conn.close()
        if row is None:
            return None
        return {"id": row["id"], "suite_id": row["suite_id"], "label": row["label"],
                "metrics": json.loads(row["metrics_json"]), "created_at": row["created_at"]}

    def history(self, suite_id: str, label: Optional[str] = None) -> List[Dict[str, Any]]:
        """Todas as baselines de uma suite (opcionalmente de um label), da mais
        antiga para a mais recente — a série temporal que o Ouroboros compara
        (delta entre runs) em vez de um único snapshot."""
        conn = self._get_connection()
        if label:
            cursor = conn.execute(
                "SELECT * FROM eval_baselines WHERE suite_id = ? AND label = ? ORDER BY created_at ASC",
                (suite_id, label),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM eval_baselines WHERE suite_id = ? ORDER BY created_at ASC",
                (suite_id,),
            )
        rows = cursor.fetchall()
        if not self._conn:
            conn.close()
        return [
            {"id": r["id"], "suite_id": r["suite_id"], "label": r["label"],
             "metrics": json.loads(r["metrics_json"]), "created_at": r["created_at"]}
            for r in rows
        ]
