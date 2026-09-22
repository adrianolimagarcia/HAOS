"""Testes unitários das funções C-ABI em Rust e fallback SQLite (GraphRAG e Canonical Store)."""

import os
from pathlib import Path
import pytest

from hermes.platform.context.memory.graphrag_store import GraphRAGStore
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore


def test_graphrag_find_related_and_search_entities_cabi(tmp_path):
    db_path = tmp_path / "graphrag.db"
    store = GraphRAGStore(db_path)

    # Inserir entidades e relações
    store.upsert_entity("AlphaService", "service", "Alpha auth service", community_id="comm_auth")
    store.upsert_entity("BetaDB", "database", "Beta primary database", community_id="comm_data")
    store.upsert_entity("GammaWorker", "worker", "Gamma background queue worker", community_id="comm_data")

    store.upsert_relation("AlphaService", "BetaDB", "queries", "reads user credentials")
    store.upsert_relation("GammaWorker", "BetaDB", "updates", "processes async updates")

    # 1. find_related (1 hop)
    res_alpha = store.find_related("AlphaService", max_hops=1)
    assert res_alpha["entity"] is not None
    assert res_alpha["entity"]["entity"] == "AlphaService"
    assert len(res_alpha["relations"]) == 1
    assert "BetaDB" in res_alpha["neighbors"]

    # 2. get_node e get_neighbors
    node = store.get_node("BetaDB")
    assert node is not None
    assert node["entity"] == "BetaDB"

    neighbors_beta = store.get_neighbors("BetaDB", max_hops=1)
    assert "AlphaService" in neighbors_beta
    assert "GammaWorker" in neighbors_beta

    # 3. find_related (2 hops a partir de AlphaService deve alcançar GammaWorker)
    res_2hops = store.find_related("AlphaService", max_hops=2)
    assert "BetaDB" in res_2hops["neighbors"]
    assert "GammaWorker" in res_2hops["neighbors"]

    # 4. search_entities (matching direto e substring)
    search_res = store.search_entities(["queue", "credentials"])
    matched_names = {e["entity"] for e in search_res}
    assert "GammaWorker" in matched_names

    # 5. Testar comportamento com fallback forçado (simulando ausência de lib nativa)
    orig_lib = GraphRAGStore._native_lib
    try:
        GraphRAGStore._native_lib = None
        fb_res = store.find_related("AlphaService", max_hops=1)
        assert fb_res["entity"]["entity"] == "AlphaService"
        assert "BetaDB" in fb_res["neighbors"]

        fb_search = store.search_entities(["auth"])
        assert any(e["entity"] == "AlphaService" for e in fb_search)
    finally:
        GraphRAGStore._native_lib = orig_lib

    store.close()


def test_canonical_read_records_and_fts_cabi(tmp_path):
    db_path = tmp_path / "canonical.db"
    store = CanonicalMemoryStore(db_path)

    rec1 = store.append(
        content="Kernel modules in HAOS use Btrfs filesystem with zstd compression.",
        scope="project",
        idempotency_key="rec-1",
        metadata={"author": "dev"},
    )
    rec2 = store.append(
        content="PostgreSQL database replication is configured for HA clusters.",
        scope="project",
        idempotency_key="rec-2",
    )
    rec3 = store.append(
        content="Temporary log files stored under /tmp/haos.",
        scope="private",
        idempotency_key="rec-3",
    )

    # 1. read_canonical_records por IDs e Scopes
    records = store.read_canonical_records([rec1.record_id, rec2.record_id, rec3.record_id], scopes=["project"])
    rec_ids = [r.record_id for r in records]
    assert rec1.record_id in rec_ids
    assert rec2.record_id in rec_ids
    # rec3 está em private, não deve vir com scope project
    assert rec3.record_id not in rec_ids

    # 2. search_fts acelerado em Rust
    fts_hits = store.search_fts("Btrfs compression", scopes=["project"])
    assert len(fts_hits) >= 1
    assert fts_hits[0].record_id == rec1.record_id

    # 3. Testar fallback quando biblioteca nativa não disponível
    orig_lib = CanonicalMemoryStore._native_lib
    try:
        CanonicalMemoryStore._native_lib = None
        fb_records = store.read_canonical_records([rec1.record_id], scopes=["project"])
        assert len(fb_records) == 1
        assert fb_records[0].record_id == rec1.record_id

        fb_fts = store.search_fts("PostgreSQL replication", scopes=["project"])
        assert len(fb_fts) >= 1
        assert fb_fts[0].record_id == rec2.record_id
    finally:
        CanonicalMemoryStore._native_lib = orig_lib

    store.close()
