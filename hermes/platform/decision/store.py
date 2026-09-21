"""Store persistente SQLite para memorização e destilação de decisões System One.

Permite que decisões avaliadas uma vez pelo Gemini 2.5 Flash Lite sejam
reutilizadas em sub-milissegundos (< 1ms) sem custo e sem rede.
"""

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes_constants import get_hermes_home
from hermes.platform.decision.spec import DecisionRecord


class DecisionStore:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = get_hermes_home() / "system_one_decisions.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_one_decisions (
                    pattern_key TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    raw_signature TEXT NOT NULL,
                    decision_type TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    last_hit_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decisions_domain ON system_one_decisions(domain)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decisions_hits ON system_one_decisions(hit_count DESC)"
            )
            conn.commit()

    @staticmethod
    def compute_key(domain: str, statement_or_question: str, context: str, options: Optional[List[str]] = None) -> str:
        """Gera chave canônica SHA-256 normalizada para identificar padrões idênticos."""
        norm_stmt = statement_or_question.strip().lower()
        norm_ctx = " ".join(context.strip().lower().split())
        opts_part = ""
        if options:
            opts_part = "|" + "|".join(sorted(o.strip().lower() for o in options))
        payload = f"{domain.lower()}::{norm_stmt}::{norm_ctx}{opts_part}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, pattern_key: str) -> Optional[Tuple[Dict[str, Any], int]]:
        """Busca decisão memorizada. Incrementa hit_count e atualiza timestamp de uso."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT result_json, hit_count FROM system_one_decisions WHERE pattern_key = ?",
                (pattern_key,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE system_one_decisions SET hit_count = hit_count + 1, last_hit_at = ? WHERE pattern_key = ?",
                (time.time(), pattern_key),
            )
            conn.commit()
            return json.loads(row["result_json"]), row["hit_count"] + 1

    def put(
        self,
        pattern_key: str,
        domain: str,
        raw_signature: str,
        decision_type: str,
        result: Dict[str, Any],
        confidence: float,
    ) -> None:
        """Salva uma nova decisão no banco para reutilização instantânea."""
        now = time.time()
        result_json = json.dumps(result)
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO system_one_decisions (
                    pattern_key, domain, raw_signature, decision_type,
                    result_json, confidence, hit_count, created_at, last_hit_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(pattern_key) DO UPDATE SET
                    result_json = excluded.result_json,
                    confidence = excluded.confidence,
                    hit_count = system_one_decisions.hit_count + 1,
                    last_hit_at = excluded.last_hit_at
                """,
                (pattern_key, domain, raw_signature, decision_type, result_json, confidence, now, now),
            )
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        """Calcula estatísticas de aprendizado e economia do System One."""
        with self._get_conn() as conn:
            total_patterns = conn.execute("SELECT COUNT(*) FROM system_one_decisions").fetchone()[0]
            total_hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) FROM system_one_decisions").fetchone()[0]
            saved_calls = max(0, total_hits - total_patterns)
            by_domain_rows = conn.execute(
                "SELECT domain, COUNT(*) as cnt, SUM(hit_count) as hits FROM system_one_decisions GROUP BY domain"
            ).fetchall()
            by_domain = {r["domain"]: {"patterns": r["cnt"], "hits": r["hits"]} for r in by_domain_rows}

            return {
                "total_learned_patterns": total_patterns,
                "total_decisions_served": total_hits,
                "saved_llm_calls": saved_calls,
                "cache_hit_rate": round(saved_calls / max(1, total_hits) * 100, 1) if total_hits > 0 else 0.0,
                "by_domain": by_domain,
            }

    def list_recent(self, limit: int = 50, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lista as decisões aprendidas mais recentes."""
        with self._get_conn() as conn:
            if domain:
                rows = conn.execute(
                    """
                    SELECT pattern_key, domain, raw_signature, decision_type,
                           result_json, confidence, hit_count, created_at, last_hit_at
                    FROM system_one_decisions
                    WHERE domain = ?
                    ORDER BY last_hit_at DESC LIMIT ?
                    """,
                    (domain, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT pattern_key, domain, raw_signature, decision_type,
                           result_json, confidence, hit_count, created_at, last_hit_at
                    FROM system_one_decisions
                    ORDER BY last_hit_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            results = []
            for r in rows:
                results.append({
                    "pattern_key": r["pattern_key"],
                    "domain": r["domain"],
                    "signature": r["raw_signature"],
                    "type": r["decision_type"],
                    "result": json.loads(r["result_json"]),
                    "confidence": r["confidence"],
                    "hit_count": r["hit_count"],
                    "created_at": r["created_at"],
                    "last_hit_at": r["last_hit_at"],
                })
            return results

    def clear(self, domain: Optional[str] = None) -> int:
        """Limpa decisões memorizadas (total ou por domínio)."""
        with self._get_conn() as conn:
            if domain:
                cur = conn.execute("DELETE FROM system_one_decisions WHERE domain = ?", (domain,))
            else:
                cur = conn.execute("DELETE FROM system_one_decisions")
            conn.commit()
            return cur.rowcount
