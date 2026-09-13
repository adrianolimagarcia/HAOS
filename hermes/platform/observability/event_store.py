"""EventStore — append-only event store (observability; NUNCA ledger de negócio).

Fase 1 (port DSH core/session): cada evento ganha um ``seq`` monotônico
por store e a ordem de leitura é determinística por ``(seq, timestamp)``,
como o log seq-contíguo do DSH exige para replay (surface.ts:341-343:
seqs contíguos e ordenados). O ``seq`` é anexado ao Event lido como
atributo transitivo (``event.seq``) — o dataclass Event não muda.

``event_id`` é a PK: anexar um evento com id repetido levanta
IntegrityError (idempotência por PK, não silêncio).

Storage (HAOS hardening):
- Uma conexão SQLite POR INSTÂNCIA, reusada em todas as operações
  (file-backed e :memory:) — elimina o custo de open/close por op.
- WAL + ``synchronous=FULL`` quando o filesystem suporta (padrão do
  kanban/state.db): durabilidade de decisão preservada — o Ouroboros
  deriva o EvolutionLedger deste stream, então NUNCA synchronous=NORMAL.
- ``busy_timeout`` alto p/ writer concorrente entre processos (WAL
  permite múltiplos leitores; um escritor por vez serializa).
- Checkpoint PASSIVE a cada ``_CHECKPOINT_EVERY_WRITES`` appends —
  mantém o ``-wal`` bounded sem bloquear leitores.
- Thread-safety: a conexão única é guardada por um RLock (o sqlite3
  module não é thread-safe por conexão); seqs continuam ``MAX+1`` sob
  ``BEGIN IMMEDIATE`` dentro do lock, logo únicos mesmo com appends
  concorrentes no mesmo processo.

Falha segura: se o filesystem não suportar WAL (NFS/SMB/virtiofs), o
``PRAGMA journal_mode=WAL`` retorna delete e o store segue operando em
rollback journal — fail-open como ``hermes_state_wal`` (sem live
downgrade; a escolha é feita uma vez na abertura).
"""

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.observability.events import Event, MessageEnvelope

logger = logging.getLogger("hermes.platform.observability.event_store")

# WAL journal_size_limit: cap do -wal entre checkpoints (espelha state.db).
_JOURNAL_SIZE_LIMIT = 64 * 1024 * 1024
# Checkpoint PASSIVE a cada N appends (espelha hermes_state.py:366).
_CHECKPOINT_EVERY_WRITES = 50
# Escritor concorrente (outro processo/instância) espera até 30s.
_BUSY_TIMEOUT_MS = 30_000


def default_event_store_path() -> Path:
    """Caminho canônico do event store do perfil ativo (``$HERMES_HOME/events.db``).

    Mesmo arquivo que o standalone webui e o master-plan-orchestrator usam, para
    que todo consumidor leia o MESMO stream.
    """
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()) / "events.db"


def get_event_store() -> "EventStore":
    """EventStore file-backed do perfil ativo.

    ``EventStore()`` sem argumento abre ``:memory:`` — um banco novo e vazio por
    processo. Um consumidor de produção que use o default enxerga zero eventos
    mesmo com o stream real cheio no disco (era o caso de todo o CLI HAOS:
    ``haos evolution status`` reportava zero para sempre). Isolamento em memória
    continua disponível: quem quer um store efêmero (testes) usa ``EventStore()``.
    """
    return EventStore(db_path=str(default_event_store_path()))


class EventStore:
    """Append-only event store (observability; NUNCA ledger de negócio).

    Uma conexão por instância, reusada; WAL + synchronous=FULL quando o
    filesystem suporta; seq monotônico ``MAX(seq)+1`` sob lock.
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._writes_since_checkpoint = 0
        self._open_connection()
        self._init_db()

    # ------------------------------------------------------------------ #
    # conexão
    # ------------------------------------------------------------------ #
    def _open_connection(self) -> sqlite3.Connection:
        """Abre (uma única vez por instância) e configura a conexão.

        WAL é tentado e confirmado pelo retorno do PRAGMA: filesystems
        WAL-unsafe retornam ``delete`` — fail-open, nunca live-downgrade.
        ``:memory:`` não usa WAL (journal memory).
        """
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        if self.db_path != ":memory:":
            try:
                row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
                mode = str(row[0]).lower() if row else ""
            except sqlite3.Error as exc:  # pragma: no cover - filesystem raro
                logger.warning("event store WAL unavailable on %s: %s",
                               self.db_path, exc)
                mode = "delete"
            if mode == "wal":
                # Durabilidade de decisão: FULL por commit. Store de auditoria
                # (EvolutionLedger deriva dele) nunca usa NORMAL.
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute(f"PRAGMA journal_size_limit={_JOURNAL_SIZE_LIMIT}")
            else:
                logger.warning("event store %s: journal_mode=%s (WAL n/d)",
                               self.db_path, mode or "unknown")
        self._conn = conn
        return conn

    def _get_connection(self) -> sqlite3.Connection:
        """Conexão única da instância (reabre se foi fechada por close())."""
        if self._conn is None:
            return self._open_connection()
        return self._conn

    def close(self) -> None:
        """Fecha a conexão da instância. Checkpoint PASSIVE final antes de
        fechar para não deixar -wal grande para trás."""
        with self._lock:
            if self._conn is None:
                return
            try:
                if self.db_path != ":memory:":
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:
                pass
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                seq INTEGER NOT NULL,
                name TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                correlation_id TEXT,
                causation_id TEXT,
                trust_level TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                payload TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_trace_seq ON events(trace_id, seq);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_events_seq_unique ON events(seq);")
            conn.commit()

    def append(self, event: Event) -> None:
        """Anexa um evento com seq ``MAX(seq)+1`` — atômico sob o RLock.

        Duplicar ``event_id`` levanta IntegrityError (idempotência por PK).
        A cada ``_CHECKPOINT_EVERY_WRITES`` appends roda um checkpoint
        PASSIVE (mantém o -wal bounded; falha nunca quebra o append).
        """
        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 AS nxt FROM events"
                ).fetchone()
                seq = int(row["nxt"])
                conn.execute(
                    """
                    INSERT INTO events (event_id, seq, name, trace_id, correlation_id, causation_id, trust_level, schema_version, timestamp, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        seq,
                        event.name,
                        event.trace_id,
                        event.correlation_id,
                        event.causation_id,
                        event.trust_level,
                        event.schema_version,
                        event.timestamp,
                        json.dumps(event.payload),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            self._writes_since_checkpoint += 1
            if self._writes_since_checkpoint >= _CHECKPOINT_EVERY_WRITES:
                self._writes_since_checkpoint = 0
                self._maybe_checkpoint(conn)

    def _maybe_checkpoint(self, conn: sqlite3.Connection) -> None:
        """Checkpoint PASSIVE best-effort; nunca levanta (append já commitou)."""
        if self.db_path == ":memory:":
            return
        try:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error as exc:  # pragma: no cover - concorrência rara
            logger.warning("event store passive checkpoint failed: %s", exc)

    def cursor(self) -> int:
        """Maior seq persistido (0 quando vazio) — ponto de retomada do replay."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS cur FROM events").fetchone()
            return int(row["cur"])

    def events_after(
        self, seq: int, trace_id: Optional[str] = None, limit: Optional[int] = None
    ) -> List[Event]:
        """Eventos com seq > ``seq``, em ordem determinística (seq, timestamp).

        Replay incremental: guarde o ``seq`` do último evento processado e
        chame com ele. ``limit`` pega a cauda (mais recentes) após o corte."""
        with self._lock:
            conn = self._get_connection()
            params: List[Any] = [seq]
            where_clause = "WHERE seq > ?"
            if trace_id is not None:
                where_clause += " AND trace_id = ?"
                params.append(trace_id)

            if limit is not None:
                # Query the tail using subquery ordered by seq DESC LIMIT ? then re-order ASC
                sql = f"SELECT * FROM (SELECT * FROM events {where_clause} ORDER BY seq DESC, timestamp DESC LIMIT ?) ORDER BY seq ASC, timestamp ASC"
                params.append(int(limit))
            else:
                sql = f"SELECT * FROM events {where_clause} ORDER BY seq ASC, timestamp ASC"

            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            return self._rows_to_events(rows)

    def _rows_to_events(self, rows) -> List[Event]:
        events = []
        for row in rows:
            event = Event(
                event_id=row["event_id"],
                name=row["name"],
                trace_id=row["trace_id"],
                correlation_id=row["correlation_id"],
                causation_id=row["causation_id"],
                trust_level=row["trust_level"],
                schema_version=row["schema_version"],
                timestamp=row["timestamp"],
                payload=json.loads(row["payload"])
            )
            event.seq = int(row["seq"])  # transitivo: asdict() não o inclui
            events.append(event)
        return events

    def get_by_trace_id(self, trace_id: str) -> List[Event]:
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT * FROM events WHERE trace_id = ? ORDER BY seq ASC, timestamp ASC",
                (trace_id,),
            )
            rows = cursor.fetchall()
            return self._rows_to_events(rows)

    def get_all(self, name: Optional[str] = None, limit: Optional[int] = None) -> List[Event]:
        """Todos os eventos (opcionalmente filtrados por nome), mais antigos
        primeiro — a janela que o Ouroboros alimentado varre por sinais."""
        with self._lock:
            conn = self._get_connection()
            params: List[Any] = []
            where_clause = ""
            if name is not None:
                where_clause = "WHERE name = ?"
                params.append(name)

            if limit is not None:
                sql = f"SELECT * FROM (SELECT * FROM events {where_clause} ORDER BY seq DESC, timestamp DESC LIMIT ?) ORDER BY seq ASC, timestamp ASC"
                params.append(int(limit))
            else:
                sql = f"SELECT * FROM events {where_clause} ORDER BY seq ASC, timestamp ASC"

            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            return self._rows_to_events(rows)

    def get_by_correlation_id(self, correlation_id: str) -> List[Event]:
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT * FROM events WHERE correlation_id = ? ORDER BY seq ASC, timestamp ASC",
                (correlation_id,),
            )
            rows = cursor.fetchall()
            return self._rows_to_events(rows)

    read_events = get_all  # Canonical ADR-002 alias
