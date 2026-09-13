"""Tests for HAOS RAGFlow Engine (Deep Document Understanding, Breadcrumbs & RRF)."""

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
