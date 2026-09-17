"""DecisionStore — decisões arquiteturais com resolução de conflito temporal.

Mantém decisões com ``valid_from``/``valid_until`` e ``supersedes``/``superseded_by``,
garantindo que o contexto nunca traga uma regra antiga e sua substituta em contradição.

**Durabilidade.** Este store é a projeção ``decisions`` do Memory Fabric, então ele
precisa sobreviver a restart como as outras: o journal canônico guarda a verdade, mas
uma projeção que evapora no restart não é projeção, é cache — e o leitor legado
(shadow mode / rollback) lê daqui. Por isso o estado vive em SQLite, com o mesmo
tratamento do store canônico e do índice vetorial (WAL + ``busy_timeout``), e
``path=None`` significa um banco em memória (útil para testes e para quem só quer um
store descartável) — um único caminho de código, sem duas implementações para divergir.

O dicionário ``_decisions`` continua existindo como **vista de leitura** sobre o banco
(``MutableMapping``), porque há leitores que o usam como mapping (``id in store._decisions``,
``store._decisions[id]``). Ele nunca é uma segunda fonte de verdade: toda escrita passa
por SQL.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.sources.base import ContextSource

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_decisions (
    decision_id     TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL,
    content         TEXT NOT NULL,
    authority       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    supersedes_json TEXT NOT NULL,
    superseded_by   TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_decisions_status ON memory_decisions(status);
"""


@dataclass
class ArchitectureDecision:
    """Decisão arquitetural tipada com controle temporal."""
    id: str  # ex: ADR-018
    title: str
    status: str  # proposed | accepted | superseded | rejected
    content: str
    authority: AuthorityLevel = AuthorityLevel.ARCHITECTURE
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d", time.gmtime()))
    supersedes: List[str] = field(default_factory=list)
    superseded_by: Optional[str] = None


class _DecisionView(MutableMapping):
    """Mapping read-through sobre o banco; nunca guarda estado próprio.

    Existe para compatibilidade com leitores que tratam ``_decisions`` como dicionário.
    Iterar ou contar vai ao banco, então a vista não pode ficar defasada em relação ao
    que foi persistido.
    """

    def __init__(self, store: "DecisionStore") -> None:
        self._store = store

    def __getitem__(self, key: str) -> ArchitectureDecision:
        decision = self._store.get_decision(key)
        if decision is None:
            raise KeyError(key)
        return decision

    def __setitem__(self, key: str, value: ArchitectureDecision) -> None:
        self._store._upsert(value)

    def __delitem__(self, key: str) -> None:
        if not self._store._delete(key):
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._store.decision_ids())

    def __len__(self) -> int:
        return self._store.count()

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self._store.has_decision(key)


class DecisionStore(ContextSource):
    """Repositório de decisões de projeto com resolução de conflito temporal."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            ":memory:" if self.path is None else str(self.path),
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)
        self._decisions: MutableMapping[str, ArchitectureDecision] = _DecisionView(self)

    # -- ciclo de vida -------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- API pública ---------------------------------------------------------

    @property
    def source_name(self) -> str:
        return "decision_store"

    def record_decision(
        self,
        decision_id: str,
        title: str,
        content: str,
        supersedes: Optional[List[str]] = None,
        authority: AuthorityLevel = AuthorityLevel.ARCHITECTURE,
    ) -> ArchitectureDecision:
        """Registra uma decisão, marcando as anteriores que ela substitui.

        Escrita idempotente (upsert por ``decision_id``): reaplicar a mesma projeção
        do outbox não duplica nem corrompe o histórico.
        """
        superseded_list = list(supersedes or [])
        decision = ArchitectureDecision(
            id=decision_id,
            title=title,
            status="accepted",
            content=content,
            authority=authority,
            supersedes=superseded_list,
        )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT INTO memory_decisions"
                    " (decision_id, title, status, content, authority, created_at, supersedes_json, superseded_by)"
                    " VALUES (?,?,?,?,?,?,?,NULL)"
                    " ON CONFLICT(decision_id) DO UPDATE SET"
                    "  title=excluded.title, status=excluded.status, content=excluded.content,"
                    "  authority=excluded.authority, supersedes_json=excluded.supersedes_json,"
                    "  superseded_by=NULL",
                    (
                        decision.id, decision.title, decision.status, decision.content,
                        decision.authority.value, decision.created_at,
                        json.dumps(decision.supersedes),
                    ),
                )
                for old_id in superseded_list:
                    self._conn.execute(
                        "UPDATE memory_decisions SET status='superseded', superseded_by=?"
                        " WHERE decision_id=?",
                        (decision_id, old_id),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return decision

    def get_decision(self, decision_id: str) -> Optional[ArchitectureDecision]:
        """Recupera uma decisão específica por ID."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
        return self._row(row) if row is not None else None

    def has_decision(self, decision_id: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM memory_decisions WHERE decision_id=? LIMIT 1", (decision_id,)
            ).fetchone() is not None

    def decision_ids(self) -> List[str]:
        """IDs em ordem de inserção (``rowid``), como o dicionário anterior fazia."""
        with self._lock:
            return [
                row[0]
                for row in self._conn.execute(
                    "SELECT decision_id FROM memory_decisions ORDER BY rowid"
                ).fetchall()
            ]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM memory_decisions").fetchone()[0]

    def retrieve(
        self,
        query: str = "",
        task_id: str = "",
        budget_hint: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ContextItem]:
        """Retorna decisões ativas (não superseded), resolvendo conflitos temporais."""
        results: List[ContextItem] = []
        q_lower = query.lower()

        for dec in self._active_decisions():
            if query and (
                q_lower not in dec.id.lower()
                and q_lower not in dec.title.lower()
                and q_lower not in dec.content.lower()
            ):
                continue

            item = ContextItem(
                id=f"decision-{dec.id}",
                item_type="architecture_decision",
                source_uri=f"decision://{dec.id}",
                content=f"# {dec.id}: {dec.title}\nStatus: {dec.status}\n\n{dec.content}",
                title=f"{dec.id} - {dec.title}",
                summary=f"{dec.id} ({dec.title}): {dec.content[:200]}...",
                abstract=f"{dec.id}: {dec.title}",
                trust=TrustLevel.ARCHITECTURE_DECISIONS,
                authority=dec.authority,
                relevance=1.0,
                supersedes=dec.supersedes,
                superseded_by=dec.superseded_by,
                metadata={"status": dec.status},
            )
            results.append(item)

        return results

    # -- internos ------------------------------------------------------------

    def _active_decisions(self) -> List[ArchitectureDecision]:
        """Decisões não superseded, em ordem de inserção.

        O filtro de texto roda em Python de propósito: o conjunto de ADRs é pequeno e
        assim a semântica de busca (substring case-insensitive em três campos) fica
        idêntica à que existia, sem depender de escaping de ``LIKE``.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_decisions WHERE status!='superseded' ORDER BY rowid"
            ).fetchall()
        return [self._row(row) for row in rows]

    def _upsert(self, decision: ArchitectureDecision) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO memory_decisions"
                " (decision_id, title, status, content, authority, created_at, supersedes_json, superseded_by)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(decision_id) DO UPDATE SET"
                "  title=excluded.title, status=excluded.status, content=excluded.content,"
                "  authority=excluded.authority, supersedes_json=excluded.supersedes_json,"
                "  superseded_by=excluded.superseded_by",
                (
                    decision.id, decision.title, decision.status, decision.content,
                    decision.authority.value, decision.created_at,
                    json.dumps(decision.supersedes), decision.superseded_by,
                ),
            )

    def _delete(self, decision_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM memory_decisions WHERE decision_id=?", (decision_id,)
            )
        return cursor.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> ArchitectureDecision:
        return ArchitectureDecision(
            id=row["decision_id"],
            title=row["title"],
            status=row["status"],
            content=row["content"],
            # Fail loud on an unknown authority: this is a rebuildable projection, so a
            # corrupt row should surface rather than silently downgrade to a default.
            authority=AuthorityLevel(row["authority"]),
            created_at=row["created_at"],
            supersedes=list(json.loads(row["supersedes_json"])),
            superseded_by=row["superseded_by"],
        )
