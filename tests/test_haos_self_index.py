"""tests/test_haos_self_index.py — T3 a T9 do SELF_INDEX_PLAN (Document-Key).

Contratos de Document-Key (haos_rag_keys) + gates T3-T9:
- T3: haos_rag_keys vazia => hybrid_search idêntico ao baseline
- T4: chave que não recupera o próprio doc no top-3 (portão especificidade) NÃO é persistida
- T5: chave com Jaccard >= 0.8 com chave de outro doc é rejeitada (portão separação)
- T6: index_document de um doc reindexado cascadeia as chaves antigas
- T7: remove_documents_missing_on_disk não deixa chave órfã
- T8: RetrievalBudget limita a passada de chaves
- T9: cascata do HybridKnowledgeRouter mantém precedência OKF determinístico antes de RAGFlow
"""

from __future__ import annotations

from pathlib import Path
import pytest

from hermes.platform.memory.ragflow_engine import (
    RAGFlowStore,
    RetrievalBudget,
    DocumentKey,
    specificity_gate,
    separation_gate,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "ragflow.db"


def test_t3_empty_keys_preserves_hybrid_search(temp_db: Path) -> None:
    """T3: haos_rag_keys vazia => busca funciona normalmente sem erro."""
    store = RAGFlowStore(db_path=temp_db)
    store.index_document(
        doc_path="adrs/ADR-001.md",
        text="# ADR-001: Arquitetura do Knowledge Store\nDecisão de usar SQLite WAL e FTS5.",
    )
    res = store.hybrid_search("ADR-001 SQLite WAL")
    assert len(res) >= 1
    assert res[0].doc_path == "adrs/ADR-001.md"


def test_t4_specificity_gate_rejects_non_retrieving_key(temp_db: Path) -> None:
    """T4: chave que não recupera o próprio doc no top-3 é rejeitada."""
    store = RAGFlowStore(db_path=temp_db)
    for i in range(5):
        store.index_document(
            doc_path=f"notes/linux_{i}.md",
            text=f"# Linux Kernel {i}\nCompilação de módulos kernel insmod modprobe driver {i}.",
        )
    store.index_document(
        doc_path="notes/recipe.md",
        text="# Receita de Bolo\nFarinha, ovos e açúcar batidos no forno.",
    )

    # Chave irrelevante para recipe.md que na busca bate nos 5 docs de linux
    bad_key = DocumentKey(
        doc_path="notes/recipe.md",
        kind="discriminative",
        key_text="compilação de módulos kernel insmod",
        generator="test",
        content_hash="abc",
    )
    assert not specificity_gate(store, bad_key, top_k=3)

    # Chave boa para recipe.md
    good_key = DocumentKey(
        doc_path="notes/recipe.md",
        kind="discriminative",
        key_text="receita de bolo farinha ovos açúcar forno",
        generator="test",
        content_hash="abc",
    )
    assert specificity_gate(store, good_key, top_k=3)


def test_t5_separation_gate_rejects_high_jaccard(temp_db: Path) -> None:
    """T5: chave com Jaccard >= 0.8 contra chave de outro doc é rejeitada."""
    store = RAGFlowStore(db_path=temp_db)
    store.index_document(
        doc_path="notes/docA.md",
        text="# Doc A\nConfiguração de rede e portas TCP UDP.",
    )
    store.index_document(
        doc_path="notes/docB.md",
        text="# Doc B\nConfiguração de rede e portas TCP UDP proxy.",
    )

    key_a = DocumentKey(
        doc_path="notes/docA.md",
        kind="discriminative",
        key_text="configuração rede portas tcp udp",
        generator="test",
        content_hash="hashA",
    )
    store.add_key(key_a)

    # Chave de B quase idêntica à de A
    key_b_dup = DocumentKey(
        doc_path="notes/docB.md",
        kind="discriminative",
        key_text="configuração rede portas tcp udp",
        generator="test",
        content_hash="hashB",
    )
    assert not separation_gate(store, key_b_dup, threshold=0.8)

    # Chave de B distinta
    key_b_distinct = DocumentKey(
        doc_path="notes/docB.md",
        kind="discriminative",
        key_text="proxy reverso firewall roteamento",
        generator="test",
        content_hash="hashB",
    )
    assert separation_gate(store, key_b_distinct, threshold=0.8)


def test_t6_reindexing_document_cascades_keys(temp_db: Path) -> None:
    """T6: reindexar documento deleta as chaves antigas associadas."""
    store = RAGFlowStore(db_path=temp_db)
    store.index_document(
        doc_path="notes/docA.md",
        text="# Doc A Versão 1\nConteúdo inicial.",
    )
    key = DocumentKey(
        doc_path="notes/docA.md",
        kind="discriminative",
        key_text="conteúdo inicial docA",
        generator="test",
        content_hash="v1",
    )
    store.add_key(key)
    assert len(store.get_keys_for_document("notes/docA.md")) == 1

    # Reindexa
    store.index_document(
        doc_path="notes/docA.md",
        text="# Doc A Versão 2\nConteúdo atualizado e modificado.",
    )
    assert len(store.get_keys_for_document("notes/docA.md")) == 0


def test_t7_remove_documents_missing_on_disk_cascades_keys(tmp_path: Path) -> None:
    """T7: remove_documents_missing_on_disk deleta chaves do documento removido."""
    doc_file = tmp_path / "note.md"
    doc_file.write_text("# Nota Temporária\nConteúdo temporário.", encoding="utf-8")

    db_path = tmp_path / "ragflow.db"
    store = RAGFlowStore(db_path=db_path)
    store.index_file(doc_file)

    key = DocumentKey(
        doc_path=str(doc_file),
        kind="discriminative",
        key_text="nota temporária conteúdo",
        generator="test",
        content_hash="tmp",
    )
    store.add_key(key)
    assert len(store.get_keys_for_document(str(doc_file))) == 1

    # Apaga o arquivo do disco e roda a limpeza
    doc_file.unlink()
    removed = store.remove_documents_missing_on_disk(tmp_path)
    assert str(doc_file) in removed
    assert len(store.get_keys_for_document(str(doc_file))) == 0


def test_t8_retrieval_budget_limits_keys_evaluation(temp_db: Path) -> None:
    """T8: RetrievalBudget interrompe avaliação de chaves quando esgotado."""
    store = RAGFlowStore(db_path=temp_db)
    for i in range(10):
        doc_path = f"notes/doc_{i}.md"
        store.index_document(doc_path=doc_path, text=f"# Doc {i}\nTermo comum alfa beta gama {i}.")
        store.add_key(DocumentKey(
            doc_path=doc_path,
            kind="discriminative",
            key_text=f"chave discriminativa termo alfa {i}",
            generator="test",
            content_hash=f"h{i}",
        ))

    store.hybrid_search("termo alfa", max_candidates=3)
    assert store.last_search_budget["hit"] is True
    assert store.last_search_budget["evaluated"] == 3


def test_t9_okf_precedence_preserved_in_router(tmp_path: Path) -> None:
    """T9: Presença de Document-Keys no RAGFlow não intercepta precedência de OKF_CANONICAL."""
    from hermes.platform.memory.okf import OKFStore
    from hermes.platform.memory.hybrid_router import HybridKnowledgeRouter

    okf_dir = tmp_path / "okf"
    okf_dir.mkdir(parents=True, exist_ok=True)
    okf_store = OKFStore(okf_dir)
    okf_store.save_document(
        title="Contrato de Autenticação",
        content="Tokens JWT expiram em 3600 segundos.",
        doc_type="contract",
        tags=["auth", "jwt"],
    )

    db_path = tmp_path / "ragflow.db"
    rag_store = RAGFlowStore(db_path=db_path)
    rag_store.index_document(
        doc_path="vault/auth.md",
        text="# Autenticação no Vault\nDetalhes de tokens JWT.",
    )
    rag_store.add_key(DocumentKey(
        doc_path="vault/auth.md",
        kind="discriminative",
        key_text="Contrato de Autenticação tokens JWT",
        generator="test",
        content_hash="h1",
    ))

    router = HybridKnowledgeRouter(okf_dir=okf_dir, ragflow_store=rag_store)
    res = router.query("Contrato de Autenticação")
    assert res["found"] is True
    assert res["source"] == "OKF_CANONICAL"
