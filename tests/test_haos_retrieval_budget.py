"""Testes de contrato do P6 — kill-switch por orçamento na recuperação.

Interpretação documentada (a spec só diz "kill-switch por orçamento"): um
limite de orçamento que INTERROMPE a recuperação quando é atingido — o custo
da busca (avaliação de candidatos no store real) fica limitado de forma
verificável, e o disparo do switch fica registrado para quem chamou. Aqui:

  * ``RetrievalBudget`` — guard puro: N consumos permitidos; a tentativa que
    excede o limite DISPARA o switch (``tripped``) e devolve False — o laço de
    avaliação para (kill-switch), nunca avalia além do orçamento;
  * ``RAGFlowStore.hybrid_search(..., max_candidates=...)`` — limita a
    avaliação de candidatos; ``last_search_budget`` registra
    ``{evaluated, hit, limit}`` por chamada (hit = switch disparou);
  * ``HybridKnowledgeRouter.query(..., retrieval_budget=...)`` — repassa o
    orçamento ao store real.

Nenhum teste lê texto de código-fonte; nenhum é change-detector. Um teste que
só passar depois da peça é o contrato de comportamento, não um snapshot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.platform.memory.hybrid_router import HybridKnowledgeRouter
from hermes.platform.memory.ragflow_engine import RAGFlowStore


def _seed(store: RAGFlowStore, n: int = 3) -> None:
    """Indexa n documentos com termos que casam com a consulta de teste."""
    for i in range(n):
        store.index_document(
            f"docs/doc-{i}.md",
            f"# Doc {i}\nMetrica de latencia p95 do servico {i} com refresh periodico.",
            doc_id=f"doc-{i}",
        )


# ── Guard puro: RetrievalBudget ──────────────────────────────────────────────

def test_retrieval_budget_consome_ate_o_limite_e_dispara_o_switch():
    from hermes.platform.memory.ragflow_engine import RetrievalBudget

    budget = RetrievalBudget(2)
    assert budget.consume() is True
    assert budget.consume() is True
    # A terceira tentativa excede o orçamento: DISPARA (kill-switch) e nega.
    assert budget.consume() is False
    assert budget.tripped is True
    assert budget.used == 2  # nunca avaliou além do orçamento


def test_retrieval_budget_sem_limite_nunca_dispara():
    from hermes.platform.memory.ragflow_engine import RetrievalBudget

    budget = RetrievalBudget(None)
    for _ in range(10):
        assert budget.consume() is True
    assert budget.tripped is False


# ── E2E: hybrid_search respeita o orçamento e registra o disparo ─────────────

def test_hybrid_search_sem_orcamento_mantem_comportamento(tmp_path):
    store = RAGFlowStore(tmp_path / "rag.db")
    _seed(store)
    results = store.hybrid_search("latencia p95 refresh")
    assert len(results) >= 1
    info = store.last_search_budget
    assert info["limit"] is None
    assert info["hit"] is False  # sem limite o switch nunca dispara


def test_hybrid_search_orcamento_minimo_dispara_e_nao_avalia_alem(tmp_path):
    store = RAGFlowStore(tmp_path / "rag.db")
    _seed(store, n=3)
    results = store.hybrid_search("latencia p95 refresh", max_candidates=1)
    info = store.last_search_budget
    assert info["limit"] == 1
    assert info["hit"] is True  # o switch disparou
    assert info["evaluated"] <= 1  # nunca avaliou além do orçamento
    # O resultado continua sendo um pool fechado de chunks válidos.
    assert isinstance(results, list)
    for chunk in results:
        assert chunk.doc_path.startswith("docs/doc-")


def test_hybrid_search_orcamento_zero_avalia_nenhum_candidato(tmp_path):
    store = RAGFlowStore(tmp_path / "rag.db")
    _seed(store, n=2)
    store.hybrid_search("latencia p95 refresh", max_candidates=0)
    info = store.last_search_budget
    assert info["limit"] == 0
    assert info["hit"] is True
    assert info["evaluated"] == 0


def test_hybrid_search_orcamento_folgado_nao_dispara(tmp_path):
    store = RAGFlowStore(tmp_path / "rag.db")
    _seed(store, n=2)
    store.hybrid_search("latencia p95 refresh", max_candidates=100)
    info = store.last_search_budget
    assert info["limit"] == 100
    assert info["hit"] is False  # orçamento suficiente: switch não disparou


# ── E2E: router repassa o orçamento ao store real ────────────────────────────

def test_router_repassa_orcamento_de_recuperacao(tmp_path):
    okf_dir = tmp_path / "okf"
    okf_dir.mkdir()
    rag_store = RAGFlowStore(tmp_path / "rag.db")
    _seed(rag_store, n=3)
    router = HybridKnowledgeRouter(okf_dir=okf_dir, ragflow_store=rag_store)

    res = router.query("latencia p95 refresh", retrieval_budget=1)
    assert res["source"] == "RAGFLOW_HYBRID"
    info = rag_store.last_search_budget
    assert info["limit"] == 1
    assert info["hit"] is True
    assert info["evaluated"] <= 1