"""Contratos da ponte RAGFlow → RAPTOR (raptor_ingest).

A árvore só existe no produto se alguém a CONSTRUÍR a partir dos chunks já
indexados. Estes testes fixam: (1) summary derivado é determinístico; (2) o
mapeamento de colunas é 1:1 e o ragflow.db NUNCA é alterado; (3) rebuild é
idempotente por corpus (put_tree substitui, não acumula).
"""
from __future__ import annotations

from hermes.platform.memory.ragflow_engine import RAGFlowStore
from hermes.platform.memory.raptor_ingest import (
    build_from_ragflow,
    chunks_from_ragflow,
    derive_summary,
)
from hermes.platform.memory.raptor_memory import RaptorStore


def test_derive_summary_deterministico_e_usa_primeira_frase():
    s = derive_summary("Adrs > ADR-012", "Latência p95 caiu. Segunda frase irrelevante.")
    assert s.startswith("Adrs > ADR-012:")
    assert "Latência p95 caiu." in s
    assert "Segunda frase" not in s
    # determinístico: mesma entrada → mesma saída
    assert s == derive_summary("Adrs > ADR-012", "Latência p95 caiu. Segunda frase irrelevante.")


def test_derive_summary_sem_header():
    assert derive_summary("", "Frase única.") == "Frase única."


def test_chunks_from_ragflow_preserva_colunas_e_nao_muta_indice(tmp_path):
    rag = RAGFlowStore(db_path=tmp_path / "rag.db")
    before = _count(rag)
    rag.index_document(
        "adrs/ADR-012.md",
        "# ADR-012\nA latência p95 do refresh caiu depois do índice FTS.",
        doc_id="adr-012",
    )
    chunks = chunks_from_ragflow(rag)
    assert chunks, "deve mapear ao menos um chunk"
    c = chunks[0]
    assert c.doc_path == "adrs/ADR-012.md"
    assert c.chunk_id  # id do banco, não gerado
    assert c.summary  # derivado, presente
    # read-only: a contagem do índice é a MESMA antes e depois da leitura
    assert _count(rag) > before  # index_document escreveu
    after = _count(rag)
    chunks_from_ragflow(rag)
    assert _count(rag) == after  # ler não muda nada


def test_build_from_ragflow_idempotente_por_corpus(tmp_path):
    rag = RAGFlowStore(db_path=tmp_path / "rag.db")
    for i in range(6):
        rag.index_document(
            f"docs/doc-{i}.md",
            f"# Doc {i}\nLatência p95 e refresh do registry espelho compartilhado.",
            doc_id=f"doc-{i}",
        )
    store = RaptorStore(db_path=tmp_path / "raptor.db")
    r1 = build_from_ragflow(rag, raptor_store=store, corpus_id="default")
    assert r1["chunks"] == _count(rag)
    assert r1["nodes"] >= r1["chunks"]  # level-0 espelha chunks + pais
    n1 = len(store.all_nodes("default"))
    # rebuild: substitui a árvore, não acumula
    r2 = build_from_ragflow(rag, raptor_store=store, corpus_id="default")
    assert len(store.all_nodes("default")) == n1
    assert r2["nodes"] == r1["nodes"]


def test_build_from_ragflow_corpus_vazio_nao_escreve(tmp_path):
    rag = RAGFlowStore(db_path=tmp_path / "rag.db")
    store = RaptorStore(db_path=tmp_path / "raptor.db")
    r = build_from_ragflow(rag, raptor_store=store, corpus_id="empty")
    assert r["chunks"] == 0 and r["nodes"] == 0
    assert store.all_nodes("empty") == []


def _count(rag: RAGFlowStore) -> int:
    with rag._get_connection() as conn:
        return conn.execute("SELECT COUNT(*) c FROM haos_rag_chunks").fetchone()["c"]
