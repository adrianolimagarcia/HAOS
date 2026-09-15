"""Tests for HAOS RAGFlow Engine (Deep Document Understanding, Breadcrumbs & RRF)."""

import sqlite3
from pathlib import Path
from hermes.platform.memory.ragflow_engine import (
    DocumentChunk,
    HeaderBreadcrumbChunker,
    ReciprocalRankFusion,
    RAGFlowStore,
)
from hermes.platform.memory.hybrid_router import HybridKnowledgeRouter
from hermes.platform.memory.reconciler import MemoryReconciler


def test_header_breadcrumb_chunker_structure():
    markdown_doc = """# Sistema de Autenticação

Visão geral da autenticação segura.

## Estrutura de Tokens

Os tokens seguem o padrão JWT com formato compacto.

### Validação de Chaves

```python
def verify_token(token: str, secret: str) -> bool:
    # Código que não deve ser quebrado
    return True
```

| Algoritmo | Chave Mínima |
|---|---|
| RS256 | 2048 bits |
| Ed25519 | 256 bits |
"""

    chunker = HeaderBreadcrumbChunker(max_chars=300)
    chunks = chunker.chunk_markdown(markdown_doc, doc_path="docs/auth.md")

    assert len(chunks) >= 2

    # Check breadcrumb hierarchy
    last_chunk = chunks[-1]
    assert "Sistema de Autenticação" in last_chunk.breadcrumb
    assert "Estrutura de Tokens" in last_chunk.breadcrumb
    assert "Validação de Chaves" in last_chunk.breadcrumb
    assert "# Sistema de Autenticação > ## Estrutura de Tokens > ### Validação de Chaves" == last_chunk.header_path

    # Check code block preservation
    assert "def verify_token" in last_chunk.content
    assert "RS256" in last_chunk.content

    # Check provenance anchor format
    assert last_chunk.provenance_anchor.startswith("[ref: docs/auth.md#L")
    assert "-L" in last_chunk.provenance_anchor

    # Check LLM formatted text
    llm_text = last_chunk.formatted_for_llm()
    assert "[Doc: docs/auth.md" in llm_text
    assert last_chunk.provenance_anchor in llm_text


def test_reciprocal_rank_fusion():
    list_a = [("doc1", 10.0), ("doc2", 8.0), ("doc3", 5.0)]
    list_b = [("doc2", 9.0), ("doc1", 7.0), ("doc4", 3.0)]

    fused = ReciprocalRankFusion.fuse([list_a, list_b], k=60)
    assert len(fused) == 4

    # doc1 and doc2 should be at the top as they appear in both lists
    top_two = [item[0] for item in fused[:2]]
    assert "doc1" in top_two
    assert "doc2" in top_two

    # Test empty input
    assert ReciprocalRankFusion.fuse([]) == []


def test_ragflow_store_index_and_hybrid_search(tmp_path):
    db_file = tmp_path / "ragflow.db"
    store = RAGFlowStore(db_path=db_file)

    doc1 = """# HAOS Architecture

## Storage Layer

HAOS utilizes SQLite in WAL mode for all transactional operations.
Zero daemons are required: no Redis, no Elasticsearch.
"""
    doc2 = """# Payment Pipeline

## Stripe Integration

Webhooks process checkout.session.completed events.
Transactions are idempotently recorded.
"""

    c1 = store.index_document("docs/arch.md", doc1)
    c2 = store.index_document("docs/payments.md", doc2)

    assert c1 >= 1
    assert c2 >= 1

    # Query for SQLite storage
    results_arch = store.hybrid_search("SQLite WAL storage zero daemons")
    assert len(results_arch) >= 1
    assert "SQLite in WAL mode" in results_arch[0].content
    assert results_arch[0].doc_path == "docs/arch.md"
    assert "Storage Layer" in results_arch[0].header_path

    # Query for Stripe payments
    results_pay = store.hybrid_search("Stripe checkout webhook")
    assert len(results_pay) >= 1
    assert "checkout.session.completed" in results_pay[0].content
    assert results_pay[0].doc_path == "docs/payments.md"


def test_hybrid_knowledge_router_with_ragflow(tmp_path):
    okf_dir = tmp_path / "okf"
    okf_dir.mkdir()
    rag_db = tmp_path / "ragflow.db"
    rec_db = tmp_path / "reconciled.db"

    rag_store = RAGFlowStore(db_path=rag_db)
    reconciler = MemoryReconciler(db_path=rec_db)

    # Index technical doc into RAGFlow
    rag_store.index_document(
        "docs/security.md",
        "# Security Model\n\n## Data Encryption\n\nAll session artifacts are encrypted at rest with AES-256-GCM."
    )

    router = HybridKnowledgeRouter(
        okf_dir=okf_dir,
        reconciler=reconciler,
        ragflow_store=rag_store,
    )

    res = router.query("AES-256-GCM encryption security")
    assert res["found"] is True
    assert res["source"] == "RAGFLOW_HYBRID"
    assert "AES-256-GCM" in res["content"]
    assert "docs/security.md" in res["doc_path"]
    assert res["provenance_anchor"].startswith("[ref: docs/security.md#L")


def _indexed_paths(db_path):
    with sqlite3.connect(db_path) as conn:
        return {r[0] for r in conn.execute("SELECT DISTINCT doc_path FROM haos_rag_chunks")}


def test_deleted_note_leaves_no_phantom_in_index_or_recall(tmp_path):
    """Nota apagada não pode sobreviver no índice nem voltar pelo recall.

    Regressão medida: indexar só acrescenta, então apagar (ou renomear) uma nota
    deixava o doc_path antigo no índice para sempre e hybrid_search devolvia um
    arquivo inexistente — em primeiro lugar, com aparência de memória válida.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    keep = vault / "mantida.md"
    gone = vault / "apagada.md"
    keep.write_text("# Nota mantida\n\nConteúdo sobre reconciliação de índice.\n", encoding="utf-8")
    gone.write_text("# Nota apagada\n\nDocumento sintético sobre tokens de indexação.\n", encoding="utf-8")

    db_path = tmp_path / "ragflow.db"
    store = RAGFlowStore(db_path=db_path)
    store.index_directory(vault)
    assert _indexed_paths(db_path) == {str(keep), str(gone)}

    gone.unlink()
    pruned = store.remove_documents_missing_on_disk(vault)

    assert pruned == [str(gone)]
    assert _indexed_paths(db_path) == {str(keep)}
    with sqlite3.connect(db_path) as conn:
        fts = {r[0] for r in conn.execute("SELECT DISTINCT doc_path FROM haos_rag_fts")}
    assert str(gone) not in fts, "linha órfã no FTS faria o recall devolver o arquivo apagado"
    assert all(
        Path(hit.doc_path).exists()
        for hit in store.hybrid_search("tokens de indexação sintético", limit=5)
    )


def test_prune_is_scoped_to_the_given_root(tmp_path):
    """Podar sob uma raiz não pode apagar o que foi indexado fora dela."""
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "fora"
    outside.mkdir()
    live = vault / "viva.md"
    live.write_text("# Viva\n\nNota que permanece.\n", encoding="utf-8")
    elsewhere = outside / "alheia.md"
    elsewhere.write_text("# Alheia\n\nIndexada de outro diretório.\n", encoding="utf-8")

    db_path = tmp_path / "ragflow.db"
    store = RAGFlowStore(db_path=db_path)
    store.index_directory(vault)
    store.index_directory(outside)
    elsewhere.unlink()

    assert store.remove_documents_missing_on_disk(vault) == []
    assert _indexed_paths(db_path) == {str(live), str(elsewhere)}
