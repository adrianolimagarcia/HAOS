"""Invariantes de recall com vigência temporal (GAP-01, GBrain Etapa 2).

Contrato (ADR-028): registro visível no recall sse status='active'
E (valid_from IS NULL OR valid_from <= now) E (valid_until IS NULL OR valid_until > now).
Intervalo semiaberto [valid_from, valid_until) — em valid_until exato NÃO aparece.
Registros legados (valid_from IS NULL no banco) passam apenas pelo filtro de status.
Relógio injetável: now determinístico, nunca time.time() implícito no teste.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore

NOW = 1000.0


@pytest.fixture()
def store() -> CanonicalMemoryStore:
    d = tempfile.mkdtemp()
    s = CanonicalMemoryStore(os.path.join(d, "m.db"))
    r_vigente = s.append(content="fato vigente", scope="project", valid_from=900.0)
    r_expirado = s.append(content="fato expirado", scope="project", valid_from=800.0)
    r_legado = s.append(content="fato legado", scope="project")
    with s._tx() as db:
        db.execute("UPDATE memory_records SET valid_until=1100.0 WHERE record_id=?", (r_vigente.record_id,))
        db.execute("UPDATE memory_records SET valid_until=900.0 WHERE record_id=?", (r_expirado.record_id,))
        # legado real: valid_from = 0 (epoch) — o schema tem NOT NULL em valid_from,
        # então "frescor desconhecido" na prática é valid_from=0 (sempre valeu).
        # O filtro SQL (valid_from IS NULL OR valid_from <= now) cobre ambos os casos.
        db.execute("UPDATE memory_records SET valid_from=0.0 WHERE record_id=?", (r_legado.record_id,))
    return s


def _ids(records):
    return {r.record_id for r in records}


def test_registro_expirado_nao_aparece_no_recall(store: CanonicalMemoryStore) -> None:
    res = store.search_fts("fato", ["project"], now=NOW)
    assert "fato expirado" not in [r.content for r in res]


def test_registro_dentro_da_vigencia_aparece(store: CanonicalMemoryStore) -> None:
    res = store.search_fts("fato", ["project"], now=NOW)
    assert "fato vigente" in [r.content for r in res]


def test_legado_sem_datas_aparece_por_status(store: CanonicalMemoryStore) -> None:
    res = store.search_fts("fato", ["project"], now=NOW)
    assert "fato legado" in [r.content for r in res]


def test_now_injetado_deterministico(store: CanonicalMemoryStore) -> None:
    a = store.search_fts("fato", ["project"], now=NOW)
    b = store.search_fts("fato", ["project"], now=NOW)
    assert _ids(a) == _ids(b)
    # agora diferente muda o resultado de forma previsível
    depois = store.search_fts("fato", ["project"], now=1200.0)
    assert "fato vigente" not in [r.content for r in depois]


def test_fronteira_valid_until_exato_nao_aparece(store: CanonicalMemoryStore) -> None:
    vigente = [r for r in store.search_fts("vigente", ["project"], now=NOW)][0]
    assert store.active_by_ids([vigente.record_id], ["project"], now=1100.0) == []
    assert store.active_by_ids([vigente.record_id], ["project"], now=1099.9) != []


def test_fronteira_valid_from_inclusivo(store: CanonicalMemoryStore) -> None:
    vigente = [r for r in store.search_fts("vigente", ["project"], now=NOW)][0]
    assert store.active_by_ids([vigente.record_id], ["project"], now=900.0) != []
    assert store.active_by_ids([vigente.record_id], ["project"], now=899.9) == []


def test_read_canonical_records_tambem_filtra_expirado(store: CanonicalMemoryStore) -> None:
    expirado = [r for r in store.search_fts("expirado", ["project"], now=800.0)]
    assert expirado, "setup: registro existia dentro da vigência"
    rid = expirado[0].record_id
    assert store.read_canonical_records([rid], ["project"], now=NOW) == []