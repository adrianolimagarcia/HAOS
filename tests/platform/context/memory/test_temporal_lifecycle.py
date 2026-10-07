"""Testes invariantes para ciclo de vida temporal e observed_at no Memory Fabric.

Invariantes cobertos:
1. Limite de validade intervalar [valid_from, valid_until) inclusivo/exclusivo.
2. Registros legados sem datas (frescor desconhecido, sem vigência inventada).
3. Migração aditiva em DB existente COM dados preservados e backfill observed_at = valid_from.
4. Relógio injetável (now: float | None) garantindo determinismo temporal.
5. Semântica observed_at vs valid_from / created_at.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore, MemoryRecord
from hermes.platform.context.memory.schemas import KnowledgeItem, KnowledgeStatus


def test_validity_interval_boundary_inclusive_start_exclusive_end() -> None:
    """Invariante: [valid_from, valid_until) — início inclusivo, fim exclusivo."""
    t_start = 1000.0
    t_end = 2000.0

    # Teste no schema KnowledgeItem
    item = KnowledgeItem(
        id="item-interval-1",
        title="Regra temporária de infra",
        kind="fact",
        content="Cluster em janela de manutenção",
        valid_from=t_start,
        valid_until=t_end,
    )

    assert not item.is_valid_at(t_start - 0.001)
    assert item.is_valid_at(t_start)  # Início inclusivo
    assert item.is_valid_at(1500.0)
    assert not item.is_valid_at(t_end)  # Fim exclusivo
    assert not item.is_valid_at(t_end + 10.0)

    # Teste no MemoryRecord do canonical_store
    record = MemoryRecord(
        record_id="rec-interval-1",
        logical_id="log-interval-1",
        revision=1,
        scope="project",
        content="Cluster em janela de manutenção",
        valid_from=t_start,
        valid_until=t_end,
    )

    assert not record.is_valid_at(t_start - 0.001)
    assert record.is_valid_at(t_start)  # Início inclusivo
    assert record.is_valid_at(1500.0)
    assert not record.is_valid_at(t_end)  # Fim exclusivo
    assert not record.is_valid_at(t_end + 10.0)


def test_legacy_record_without_dates_no_fabricated_validity() -> None:
    """Invariante: Registro legado sem datas tem frescor desconhecido, nunca inventar vigência."""
    # KnowledgeItem legado
    legacy_item = KnowledgeItem(
        id="item-legacy-1",
        title="Fato histórico legado",
        kind="fact",
        content="PostgreSQL configurado na porta 5432",
        valid_from=None,
        valid_until=None,
    )

    # Válido em qualquer instante temporal a menos que explicitamente superseded
    assert legacy_item.is_valid_at(0.0)
    assert legacy_item.is_valid_at(9999999999.0)
    assert legacy_item.is_active_at(now=1700000000.0)

    # Quando superseded, deixa de ser válido sem precisar de valid_until inventado
    legacy_item.superseded_by = "item-legacy-2"
    assert not legacy_item.is_valid_at(1700000000.0)
    assert not legacy_item.is_active_at(now=1700000000.0)

    # MemoryRecord com valid_from=0.0 e valid_until=None
    legacy_record = MemoryRecord(
        record_id="rec-legacy-1",
        logical_id="log-legacy-1",
        revision=1,
        scope="project",
        content="PostgreSQL configurado na porta 5432",
        valid_from=0.0,
        valid_until=None,
    )
    assert legacy_record.is_valid_at(100.0)
    assert legacy_record.is_valid_at(9999999999.0)
    assert legacy_record.is_active_at(now=1700000000.0)


def test_injectable_clock_deterministic_validity() -> None:
    """Invariante: Funções de validade recebem now: float | None para testes determinísticos."""
    t_fixed = 1750000000.0
    t_expired = 1740000000.0

    item = KnowledgeItem(
        id="item-clock-1",
        title="Token de sessão efêmero",
        kind="fact",
        content="Token de sessão transitório",
        valid_from=1700000000.0,
        valid_until=t_expired,
    )

    # Com relógio no passado antes da expiração: ativo e válido
    clock_past = 1730000000.0
    assert item.is_valid_at(now=clock_past)
    assert item.status_at(now=clock_past) == KnowledgeStatus.ACTIVE
    assert item.is_active_at(now=clock_past)

    # Com relógio no futuro após expiração: inativo e superseded no tempo
    clock_future = t_fixed
    assert not item.is_valid_at(now=clock_future)
    assert item.status_at(now=clock_future) == KnowledgeStatus.SUPERSEDED
    assert not item.is_active_at(now=clock_future)


def test_additive_migration_on_existing_db_preserves_data(tmp_path: Path) -> None:
    """Invariante: Migração aditiva em DB existente com dados preservados e backfill de observed_at."""
    db_file = tmp_path / "legacy_memory.db"

    # 1. Cria schema legado v2 SEM a coluna observed_at
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE memory_schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO memory_schema_version VALUES (2)")
    conn.execute("""
        CREATE TABLE memory_records (
            record_id TEXT PRIMARY KEY,
            logical_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            scope TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            confidence REAL NOT NULL,
            provenance_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            valid_from REAL NOT NULL,
            valid_until REAL,
            supersedes_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(logical_id, revision)
        )
    """)
    # Insere 3 registros legados com datas distintas
    conn.execute("""
        INSERT INTO memory_records VALUES
        ('rec-1', 'log-1', 1, 'project', 'fact', 'active', 'Fato A', 'hash-a', 1.0, '[]', '{}', 1000.0, NULL, '[]', 1005.0),
        ('rec-2', 'log-2', 1, 'project', 'fact', 'active', 'Fato B', 'hash-b', 0.9, '[]', '{}', 2000.0, 3000.0, '[]', 2005.0),
        ('rec-3', 'log-3', 1, 'project', 'fact', 'superseded', 'Fato C', 'hash-c', 0.8, '[]', '{}', 500.0, 1000.0, '[]', 505.0)
    """)
    conn.commit()
    conn.close()

    # 2. Inicializa CanonicalMemoryStore apontando para o DB legado (dispara _migrate())
    store = CanonicalMemoryStore(db_file)

    # 3. Verifica se todos os registros foram preservados e observed_at preenchido com valid_from
    rec1 = store.get("rec-1")
    assert rec1 is not None
    assert rec1.content == "Fato A"
    assert rec1.valid_from == 1000.0
    assert rec1.observed_at == 1000.0  # Backfill a partir de valid_from

    rec2 = store.get("rec-2")
    assert rec2 is not None
    assert rec2.content == "Fato B"
    assert rec2.valid_from == 2000.0
    assert rec2.valid_until == 3000.0
    assert rec2.observed_at == 2000.0

    all_records = store.list_records(include_superseded=True)
    assert len(all_records) == 3

    # 4. Grava novo registro explicitando observed_at != valid_from
    # Cenário: Fato ocorrido em 1990 (observed_at), mas registrado no sistema agora (valid_from)
    t_world = 631152000.0  # 1990-01-01
    t_system = 1700000000.0  # Sistema atual
    new_rec = store.append(
        content="Fundação da empresa em 1990",
        scope="project",
        valid_from=t_system,
        observed_at=t_world,
        idempotency_key="rec-foundation",
    )
    assert new_rec.valid_from == t_system
    assert new_rec.observed_at == t_world

    reloaded_new = store.get("rec-foundation")
    assert reloaded_new is not None
    assert reloaded_new.valid_from == t_system
    assert reloaded_new.observed_at == t_world

    # 5. Valida via query SQLite direta integridade das colunas
    direct_conn = sqlite3.connect(str(db_file))
    cols = [r[1] for r in direct_conn.execute("PRAGMA table_info(memory_records)").fetchall()]
    assert "observed_at" in cols
    null_count = direct_conn.execute("SELECT count(*) FROM memory_records WHERE observed_at IS NULL").fetchone()[0]
    assert null_count == 0
    direct_conn.close()

    store.close()
