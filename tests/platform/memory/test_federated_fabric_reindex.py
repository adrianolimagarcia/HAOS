"""Testes de reindexação vetorial incremental e idempotente para FederatedMemoryCoordinator.

Validações obrigatórias (HD-03):
1) Primeira indexação a partir de fabric.db temporário preenche os vetores no índice vetorial temporário;
2) Idempotência: re-executar com os mesmos dados é no-op e não duplica registros nem gera inconsistência;
3) Incrementalidade: novos dados adicionados ao fabric são indexados incrementalmente sem reprocessar os existentes;
4) Isolamento total: nenhum banco de produção (/root/.haos/memory/*) é tocado, operando estritamente em tmp_path;
5) Suporte a force=True quando reindexação forçada é requisitada.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Sequence, Tuple

import pytest

from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.embedding import EmbeddingModel
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.context.memory.vector_index import SQLiteVectorIndex


class MockTrackingEmbedder:
    """Mock embedder determinístico que contabiliza chamadas para demonstrar incrementalidade."""

    def __init__(self, model_id: str = "mock-embedder-v1", dimensions: int = 4) -> None:
        self._model = EmbeddingModel(model_id=model_id, dimensions=dimensions)
        self.call_count = 0
        self.embedded_texts: List[str] = []

    @property
    def model(self) -> EmbeddingModel:
        return self._model

    def embed(self, text: str) -> Tuple[float, ...]:
        self.call_count += 1
        self.embedded_texts.append(text)
        val = float(len(text) % 10) / 10.0
        return (val, 1.0 - val, 0.5, 0.5)


@pytest.fixture()
def temp_fabric_env(tmp_path: Path):
    """Cria um ambiente completamente isolado e temporário para o FederatedMemoryCoordinator."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)

    # Garante que nenhum caminho de produção seja usado
    assert not str(tmp_path).startswith("/root/.haos")

    embedder = MockTrackingEmbedder()

    # Cria canonical store e vector index apontando explicitamente para tmp_path
    canonical_store = CanonicalMemoryStore(ledger_dir / "memory" / "fabric.db")
    vector_index = SQLiteVectorIndex(
        ledger_dir / "memory" / "vectors.db",
        embedder.model.model_id,
        dimensions=embedder.model.dimensions,
    )

    coordinator = FederatedMemoryCoordinator(
        vault_path=vault_dir,
        canonical_store=canonical_store,
        vector_index=vector_index,
        embedder=embedder,
        auto_start_worker=False,
    )

    yield {
        "tmp_path": tmp_path,
        "vault_dir": vault_dir,
        "ledger_dir": ledger_dir,
        "coordinator": coordinator,
        "canonical_store": canonical_store,
        "vector_index": vector_index,
        "embedder": embedder,
    }

    coordinator.close()


def test_reindex_vectors_populates_vectors_and_is_idempotent(temp_fabric_env):
    """Testa passos 1 e 2:

    1) Primeira indexação preenche os vetores a partir do canonical store temporário.
    2) Segunda indexação com mesmos dados é no-op (idempotente) e não duplica registros.
    """
    coordinator: FederatedMemoryCoordinator = temp_fabric_env["coordinator"]
    canonical_store: CanonicalMemoryStore = temp_fabric_env["canonical_store"]
    vector_index: SQLiteVectorIndex = temp_fabric_env["vector_index"]
    embedder: MockTrackingEmbedder = temp_fabric_env["embedder"]

    # Injeta 3 registros diretamente no canonical store (simulando fabric.db preexistente sem projeção vetorial)
    r1 = canonical_store.append(content="Arquitetura de microsserviços HAOS", scope="project")
    r2 = canonical_store.append(content="Regra operacional de backup no fabric", scope="global")
    r3 = canonical_store.append(content="Decisão técnica ADR-003", scope="team")

    assert len(canonical_store.list_records()) == 3
    # Inicialmente, o índice vetorial está vazio
    assert vector_index.count() == 0
    assert coordinator.pending_vector_reindex() == 3

    # 1. Executa primeira reindexação
    indexed_count = coordinator.reindex_vectors()
    assert indexed_count == 3
    assert vector_index.count() == 3
    assert coordinator.pending_vector_reindex() == 0
    assert embedder.call_count == 3

    # Verifica que os IDs indexados correspondem aos registros canônicos
    indexed_ids = vector_index.indexed_ids()
    assert indexed_ids == {r1.record_id, r2.record_id, r3.record_id}

    # 2. Executa segunda reindexação (idempotência)
    second_pass_count = coordinator.reindex_vectors()
    assert second_pass_count == 0, "Segunda execução com dados inalterados deve retornar 0 registros indexados"
    assert vector_index.count() == 3, "Total de vetores não deve duplicar nem alterar"
    assert coordinator.pending_vector_reindex() == 0
    assert embedder.call_count == 3, "Embedder não deve ser chamado novamente para registros já indexados"

    # Alias sync_vectors também deve ser estritamente idempotente
    sync_pass_count = coordinator.sync_vectors()
    assert sync_pass_count == 0
    assert vector_index.count() == 3
    assert embedder.call_count == 3


def test_reindex_vectors_incremental_indexing(temp_fabric_env):
    """Testa passo 3:

    Novos dados adicionados ao fabric são indexados incrementalmente sem reprocessar os já existentes.
    """
    coordinator: FederatedMemoryCoordinator = temp_fabric_env["coordinator"]
    canonical_store: CanonicalMemoryStore = temp_fabric_env["canonical_store"]
    vector_index: SQLiteVectorIndex = temp_fabric_env["vector_index"]
    embedder: MockTrackingEmbedder = temp_fabric_env["embedder"]

    # Fase 1: 2 registros iniciais
    r1 = canonical_store.append(content="Fato Alpha inicial", scope="global")
    r2 = canonical_store.append(content="Fato Beta inicial", scope="team")

    indexed_first = coordinator.reindex_vectors()
    assert indexed_first == 2
    assert vector_index.count() == 2
    assert embedder.call_count == 2
    assert coordinator.pending_vector_reindex() == 0

    # Fase 2: Adiciona 2 novos registros ao fabric
    r3 = canonical_store.append(content="Novo Fato Gamma incremental", scope="project")
    r4 = canonical_store.append(content="Novo Fato Delta incremental", scope="private")

    assert len(canonical_store.list_records()) == 4
    # pending_vector_reindex identifica exatamente os 2 novos
    assert coordinator.pending_vector_reindex() == 2

    # Executa reindexação incremental
    indexed_second = coordinator.reindex_vectors()
    assert indexed_second == 2, "Apenas os 2 novos registros devem ser reindexados"
    assert vector_index.count() == 4, "Total no índice vetorial deve ser 4"
    assert coordinator.pending_vector_reindex() == 0

    # O embedder foi chamado 2 vezes na 1ª fase + 2 vezes na 2ª fase = 4 vezes
    assert embedder.call_count == 4
    assert set(embedder.embedded_texts[-2:]) == {"Novo Fato Gamma incremental", "Novo Fato Delta incremental"}


def test_reindex_vectors_force_flag(temp_fabric_env):
    """Testa que force=True re-executa a indexação para todos os registros sem duplicá-los no SQLite."""
    coordinator: FederatedMemoryCoordinator = temp_fabric_env["coordinator"]
    canonical_store: CanonicalMemoryStore = temp_fabric_env["canonical_store"]
    vector_index: SQLiteVectorIndex = temp_fabric_env["vector_index"]
    embedder: MockTrackingEmbedder = temp_fabric_env["embedder"]

    canonical_store.append(content="Dado único para teste de force", scope="global")
    assert coordinator.reindex_vectors() == 1
    assert embedder.call_count == 1
    assert vector_index.count() == 1

    # Com force=True, reindexa mesmo já presente
    assert coordinator.reindex_vectors(force=True) == 1
    assert embedder.call_count == 2
    # No SQLite, devido a chave primária / ON CONFLICT, continua 1 registro
    assert vector_index.count() == 1


def test_production_isolation_and_temporary_fixtures_only(temp_fabric_env):
    """Testa passo 4:

    Garante que nenhum arquivo no diretório de produção do usuário (/root/.haos/memory)
    é acessado ou modificado durante as operações de teste.
    """
    coordinator: FederatedMemoryCoordinator = temp_fabric_env["coordinator"]
    vector_index: SQLiteVectorIndex = temp_fabric_env["vector_index"]
    canonical_store: CanonicalMemoryStore = temp_fabric_env["canonical_store"]
    tmp_path: Path = temp_fabric_env["tmp_path"]

    # Verifica os caminhos físicos dos bancos
    vector_db_path = str(vector_index.path)
    canonical_db_path = str(canonical_store.path)

    assert str(tmp_path) in vector_db_path
    assert str(tmp_path) in canonical_db_path
    assert "/root/.haos" not in vector_db_path
    assert "/root/.haos" not in canonical_db_path

    # Ingestão e reindexação no ambiente temporário
    canonical_store.append(content="Isolamento verificado", scope="global")
    indexed = coordinator.reindex_vectors()
    assert indexed == 1
    assert vector_index.count() == 1
