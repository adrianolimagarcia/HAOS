"""GraphRAGStore — Store canônico SQLite do grafo GraphRAG (GOV-008).

O GraphRAG é uma projeção relacional derivada do corpus canônico; esta store
é a cópia PERSISTENTE dessa projeção (entidades, arestas tipadas e resumos de
comunidade) — o espelho durável do ``GraphRAGAdapter`` em memória:

- A stack A (``IncrementalGraphRAGUpdater``) GRAVA aqui enquanto processa
  KnowledgeEvents (upsert por PK, remoção em NOTE_DELETED, supersessão
  temporal ``superseded_by`` quando um evento declara ``supersedes``).
- A stack B (``GraphRAGClient`` em ``hermes/platform/memory/graphrag.py``) LÊ
  daqui para ``graphrag_query`` devolver resultados reais, sem depender de
  ``entities.csv`` feito à mão.

Convenções herdadas do EventStore/kanban (ver
``hermes/platform/observability/event_store.py``): uma conexão por instância
guardada por RLock, WAL + ``busy_timeout`` alto, idempotência por PK
(``INSERT ... ON CONFLICT DO UPDATE``). Local default: ``$HERMES_HOME/memory/
graphrag.db`` — o MESMO caminho para a stack A escrever e a stack B ler;
``get_hermes_home`` é lido lazy (per-call) para respeitar override de perfil
e a isolação de HERMES_HOME dos testes.

NOTA (limite conhecido): o esquema não guarda provenance por URI — quem
extraiu cada entidade/aresta. A remoção por NOTE_DELETED depende do rastreio
``_uri_*`` em memória do updater (igual ao ``GraphRAGAdapter``); após um
restart com o updater frio, eventos de deleção não têm como saber o que a URI
contribuiu. Isso é coerente com a projeção em memória existente e fica
documentado — o grafo sobrevive a reinícios para LEITURA, que é o contrato.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Escritor concorrente entre processos espera até 30s (espelha event_store).
_BUSY_TIMEOUT_MS = 30_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    entity TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    community_id TEXT,
    superseded_by TEXT,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_community ON entities(community_id);
CREATE TABLE IF NOT EXISTS relations (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    PRIMARY KEY (source, target, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target);
CREATE TABLE IF NOT EXISTS communities (
    community_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS entity_sources (
    entity TEXT NOT NULL, source_uri TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'global',
    PRIMARY KEY (entity, source_uri)
);
CREATE TABLE IF NOT EXISTS relation_sources (
    source TEXT NOT NULL, target TEXT NOT NULL, relation_type TEXT NOT NULL,
    source_uri TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'global',
    PRIMARY KEY (source, target, relation_type, source_uri)
);
CREATE TABLE IF NOT EXISTS applied_events (
    event_id TEXT PRIMARY KEY, applied_at REAL NOT NULL
);
"""


def default_graphrag_db_path() -> Path:
    """Caminho canônico do store: ``$HERMES_HOME/memory/graphrag.db``.

    Função (não constante de módulo) para que HERMES_HOME seja lido no
    momento da chamada — perfil ativo e isolação de testes mudam a env var.
    """
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()) / "memory" / "graphrag.db"


class GraphRAGStore:
    """Store SQLite canônico do grafo (entidades/arestas/comunidades).

    Uma conexão por instância, reusada; mutações sob RLock; idempotência por
    PK (upsert nunca levanta em repetição); WAL com ``busy_timeout`` alto para
    writer concorrente entre processos (leitor na stack B, writer na stack A).
    """

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is None:
            db_path = default_graphrag_db_path()
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._open_connection()
        self._init_db()

    # ------------------------------------------------------------------ #
    # conexão
    # ------------------------------------------------------------------ #
    def _open_connection(self) -> sqlite3.Connection:
        """Abre a conexão única da instância; WAL quando o FS suporta.

        Filesystems WAL-unsafe (NFS/SMB/virtiofs) retornam ``delete`` no
        PRAGMA — fail-open em rollback journal, nunca live-downgrade
        (espelha ``EventStore._open_connection``).
        """
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        if self.db_path != ":memory:":
            try:
                row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
                mode = str(row[0]).lower() if row else ""
            except sqlite3.Error as exc:  # pragma: no cover - FS raro
                logger.warning("graphrag store WAL unavailable on %s: %s",
                               self.db_path, exc)
                mode = "delete"
            if mode != "wal":
                logger.warning("graphrag store %s: journal_mode=%s (WAL n/d)",
                               self.db_path, mode or "unknown")
        self._conn = conn
        return conn

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            return self._open_connection()
        return self._conn

    def close(self) -> None:
        """Fecha a conexão; checkpoint PASSIVE antes para não deixar -wal."""
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

    def __enter__(self) -> "GraphRAGStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # schema
    # ------------------------------------------------------------------ #
    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            conn.executescript(_SCHEMA)
            conn.commit()

    # ------------------------------------------------------------------ #
    # escrita (idempotente por PK)
    # ------------------------------------------------------------------ #
    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        description: str = "",
        community_id: Optional[str] = None,
    ) -> None:
        """Upsert de entidade por ``entity`` (PK); mantém superseded_by."""
        now = time.time()
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO entities (entity, entity_type, description, community_id, superseded_by, updated_at)
                VALUES (?, ?, ?, ?, NULL, ?)
                ON CONFLICT(entity) DO UPDATE SET
                    entity_type = excluded.entity_type,
                    description = excluded.description,
                    community_id = excluded.community_id,
                    updated_at = excluded.updated_at
                """,
                (name, entity_type, description, community_id, now),
            )
            conn.commit()

    def upsert_relation(
        self,
        source: str,
        target: str,
        relation_type: str,
        description: str = "",
    ) -> None:
        """Upsert de aresta tipada por PK (source,target,relation_type)."""
        now = time.time()
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO relations (source, target, relation_type, description, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, target, relation_type) DO UPDATE SET
                    description = excluded.description
                """,
                (source, target, relation_type, description, now),
            )
            conn.commit()

    def upsert_community(self, community_id: str, summary: str) -> None:
        """Upsert de resumo de comunidade por PK, mesclando incrementos.

        Mescla por acréscimo quando o resumo novo ainda não está contido no
        existente — idempotente por construção (reprocessar o mesmo evento
        não duplica), ao contrário do merge por append do adapter em memória.
        """
        now = time.time()
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT summary FROM communities WHERE community_id = ?",
                (community_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO communities (community_id, summary, updated_at) VALUES (?, ?, ?)",
                    (community_id, summary, now),
                )
            else:
                existing = str(row["summary"] or "")
                merged = existing if (not summary or summary in existing) else f"{existing}\n{summary}"
                conn.execute(
                    "UPDATE communities SET summary = ?, updated_at = ? WHERE community_id = ?",
                    (merged, now, community_id),
                )
            conn.commit()

    def mark_superseded(self, entity: str, by_entity: str) -> None:
        """Marca supersessão temporal: ``entity.superseded_by = by_entity``.

        Semântica do GOV-008: fato obsoleto é supersedido, não duplicado nem
        apagado — a linha permanece com o ponteiro temporal.
        """
        now = time.time()
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                "UPDATE entities SET superseded_by = ?, updated_at = ? WHERE entity = ?",
                (by_entity, now, entity),
            )
            conn.commit()

    def remove_entities(self, names: Iterable[str]) -> int:
        """Remove entidades pelos nomes; retorna quantas existiam."""
        names = [n for n in names if n]
        if not names:
            return 0
        removed = 0
        with self._lock:
            conn = self._get_connection()
            for name in names:
                cur = conn.execute("DELETE FROM entities WHERE entity = ?", (name,))
                removed += cur.rowcount
            conn.commit()
        return removed

    def remove_relations(
        self, keys: Iterable[Tuple[str, str, str]]
    ) -> int:
        """Remove arestas por PK (source,target,relation_type)."""
        keys = [k for k in keys if all(k)]
        if not keys:
            return 0
        removed = 0
        with self._lock:
            conn = self._get_connection()
            for source, target, relation_type in keys:
                cur = conn.execute(
                    "DELETE FROM relations WHERE source = ? AND target = ? AND relation_type = ?",
                    (source, target, relation_type),
                )
                removed += cur.rowcount
            conn.commit()
        return removed

    def apply_event(
        self, event_id: str, event_type: str, uri: str,
        entities: Sequence[Tuple[str, str, str]],
        relations: Sequence[Tuple[str, str, str, str]],
        scope: str = "global",
    ) -> bool:
        """Aplica uma projeção inteira uma vez, substituindo claims da URI atomicamente."""
        if not event_id or not uri or scope not in {"private", "team", "project", "global"}:
            raise ValueError("event_id, uri and valid scope are required")
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("SELECT 1 FROM applied_events WHERE event_id=?", (event_id,)).fetchone():
                    conn.rollback()
                    return False
                if event_type == "NOTE_DELETED":
                    conn.execute("DELETE FROM entity_sources WHERE source_uri=?", (uri,))
                    conn.execute("DELETE FROM relation_sources WHERE source_uri=?", (uri,))
                else:
                    conn.execute("DELETE FROM entity_sources WHERE source_uri=?", (uri,))
                    conn.execute("DELETE FROM relation_sources WHERE source_uri=?", (uri,))
                    for name, kind, description in entities:
                        conn.execute("INSERT OR REPLACE INTO entity_sources(entity,source_uri,scope) VALUES(?,?,?)", (name, uri, scope))
                        conn.execute("INSERT INTO entities(entity,entity_type,description,updated_at) VALUES(?,?,?,?) ON CONFLICT(entity) DO UPDATE SET entity_type=excluded.entity_type, description=excluded.description, updated_at=excluded.updated_at", (name, kind, description, time.time()))
                    for source, target, relation_type, description in relations:
                        conn.execute("INSERT OR REPLACE INTO relation_sources(source,target,relation_type,source_uri,scope) VALUES(?,?,?,?,?)", (source, target, relation_type, uri, scope))
                        conn.execute("INSERT OR REPLACE INTO relations(source,target,relation_type,description,created_at) VALUES(?,?,?,?,?)", (source, target, relation_type, description, time.time()))
                conn.execute("DELETE FROM entities WHERE entity NOT IN (SELECT entity FROM entity_sources)")
                conn.execute("DELETE FROM relations WHERE NOT EXISTS (SELECT 1 FROM relation_sources rs WHERE rs.source=relations.source AND rs.target=relations.target AND rs.relation_type=relations.relation_type)")
                conn.execute("INSERT INTO applied_events(event_id,applied_at) VALUES(?,?)", (event_id, time.time()))
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    # ------------------------------------------------------------------ #
    def get_entity(self, name: str) -> Optional[Dict[str, object]]:
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT entity, entity_type, description, community_id, superseded_by, updated_at "
                "FROM entities WHERE entity = ?",
                (name,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_entities(self) -> List[Dict[str, object]]:
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT entity, entity_type, description, community_id, superseded_by, updated_at "
                "FROM entities ORDER BY entity"
            ).fetchall()
        return [dict(r) for r in rows]

    def source_uris(self) -> List[str]:
        """Distinct ``source_uri`` values that produced entities.

        ``list_entities`` does not carry the source, so this is how a caller answers
        "was record X ever projected into the graph?" — the join lives in
        ``entity_sources``, which ``apply_event`` populates per incoming URI.
        """
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT DISTINCT source_uri FROM entity_sources ORDER BY source_uri"
            ).fetchall()
        return [str(r["source_uri"]) for r in rows if r["source_uri"]]

    def list_relations(self) -> List[Dict[str, object]]:
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT source, target, relation_type, description, created_at "
                "FROM relations ORDER BY source, target, relation_type"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_communities(self) -> List[Dict[str, object]]:
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT community_id, summary, updated_at FROM communities ORDER BY community_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def search_entities(self, terms: Sequence[str]) -> List[Dict[str, object]]:
        """Entidades cujo nome/descrição/comunidade contém qualquer termo.

        Não filtra supersedidas: a linha permanece com o ponteiro
        ``superseded_by`` para o leitor decidir (informação preservada).

        Varredura linear em Python, medida a ~3,6 µs por entidade: 0,3 ms no grafo real
        (55 entidades), 3,9 ms com 1k, 17 ms com 5k e 178 ms com 50k. Não vale indexar
        agora — seria otimizar 360x acima do tamanho observado. Um ``LIKE '%termo%'``
        também não ajudaria, porque nenhum índice serve busca por substring; se um dia
        incomodar, o caminho é FTS5, como o journal já faz em ``memory_fts``.
        """
        terms = [t.strip().lower() for t in terms if t and t.strip()]
        rows = self.list_entities()
        if not terms:
            return rows
        matched = []
        for row in rows:
            haystack = " ".join(
                str(row.get(k) or "") for k in ("entity", "description", "community_id")
            ).lower()
            if any(t in haystack for t in terms):
                matched.append(row)
        return matched

    def counts(self) -> Dict[str, int]:
        """Contagem por tabela — asserção de contrato em testes/E2E."""
        with self._lock:
            conn = self._get_connection()
            return {
                "entities": conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                "relations": conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
                "communities": conn.execute("SELECT COUNT(*) FROM communities").fetchone()[0],
            }
