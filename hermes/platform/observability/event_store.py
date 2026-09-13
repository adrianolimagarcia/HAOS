import sqlite3
import json
from typing import List, Dict, Any, Optional
from hermes.platform.observability.events import Event, MessageEnvelope

class EventStore:
    """Append-only event store (observability; NUNCA ledger de negócio).

    Fase 1 (port DSH core/session): cada evento ganha um ``seq`` monotônico
    por store e a ordem de leitura é determinística por ``(seq, timestamp)``,
    como o log seq-contíguo do DSH exige para replay (surface.ts:341-343:
    seqs contíguos e ordenados). O ``seq`` é anexado ao Event lido como
    atributo transitivo (``event.seq``) — o dataclass Event não muda.

    ``event_id`` é a PK: anexar um evento com id repetido levanta
    IntegrityError (idempotência por PK, não silêncio).
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn = None
        if self.db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _get_connection(self):
        if self._conn:
            return self._conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
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
        if not self._conn:
            conn.close()

    def append(self, event: Event):
        conn = self._get_connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS nxt FROM events").fetchone()
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
                    json.dumps(event.payload)
                )
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if not self._conn:
                conn.close()

    def cursor(self) -> int:
        """Maior seq persistido (0 quando vazio) — ponto de retomada do replay."""
        conn = self._get_connection()
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS cur FROM events").fetchone()
        cursor = int(row["cur"])
        if not self._conn:
            conn.close()
        return cursor

    def events_after(
        self, seq: int, trace_id: Optional[str] = None, limit: Optional[int] = None
    ) -> List[Event]:
        """Eventos com seq > ``seq``, em ordem determinística (seq, timestamp).

        Replay incremental: guarde o ``seq`` do último evento processado e
        chame com ele. ``limit`` pega a cauda (mais recentes) após o corte."""
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
        events = self._rows_to_events(rows)
        if not self._conn:
            conn.close()
        return events

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
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM events WHERE trace_id = ? ORDER BY seq ASC, timestamp ASC",
            (trace_id,),
        )
        rows = cursor.fetchall()
        events = self._rows_to_events(rows)
        if not self._conn:
            conn.close()
        return events

    def get_all(self, name: Optional[str] = None, limit: Optional[int] = None) -> List[Event]:
        """Todos os eventos (opcionalmente filtrados por nome), mais antigos
        primeiro — a janela que o Ouroboros alimentado varre por sinais."""
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
        events = self._rows_to_events(rows)
        if not self._conn:
            conn.close()
        return events

    def get_by_correlation_id(self, correlation_id: str) -> List[Event]:
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM events WHERE correlation_id = ? ORDER BY seq ASC, timestamp ASC",
            (correlation_id,),
        )
        rows = cursor.fetchall()
        events = self._rows_to_events(rows)
        if not self._conn:
            conn.close()
        return events

    read_events = get_all  # Canonical ADR-002 alias
