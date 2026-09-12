"""Testes de contrato do P7 — A/B de fusao: RRF vs mistura linear.

Interpretação documentada (a spec só diz "A/B de RRF contra a mistura
linear"): os dois fusores existem e são comparáveis sobre as MESMAS listas
ranqueadas do store real, com métrica definida e resultado reportado — sem
escolher vencedor no código (comparar é o entregável; a escolha fica para
decisão humana com o número na mesa). Aqui:

  * ``LinearScoreFusion`` — a mistura linear: combinação ponderada de scores
    NORMALIZADOS por lista (min-max), pool fechado, determinística — a
    contraparte score-based do RRF (rank-based);
  * ``RAGFlowStore.rank_lists`` — expõe as listas ranqueadas cruas (FTS5
    BM25 + lexical) que o ``hybrid_search`` funde, para os dois fusores
    receberem EXATAMENTE a mesma entrada;
  * ``RAGFlowStore.chunks_by_id`` — resolução fechada de ids fundidos para
    DocumentChunk (doc_path), reutilizada pelo harness do A/B.

Nenhum teste lê texto de código-fonte; nenhum é change-detector.
"""

from __future__ import annotations

from hermes.platform.memory.ragflow_engine import (
    LinearScoreFusion,
    RAGFlowStore,
)


def _seed(store: RAGFlowStore) -> None:
    store.index_document(
        "docs/doc-a.md",
        "# Alpha\nProtocolAdapter depends on ModelResolver with refresh.",
        doc_id="doc-a",
    )
    store.index_document(
        "docs/doc-b.md",
        "# Beta\nProtocolAdapter is mentioned but the phrase differs.",
        doc_id="doc-b",
    )


# ── LinearScoreFusion (mistura linear) ───────────────────────────────────────

def test_linear_fusion_pool_fechado_e_escore_fechado():
    list_a = [("doc1", 10.0), ("doc2", 8.0)]
    list_b = [("doc2", 9.0), ("doc1", 7.0)]
    fused = LinearScoreFusion.fuse([list_a, list_b], weights=[1.0, 1.0])
    ids = {item for item, _ in fused}
    # Pool fechado: só ids das listas de entrada.
    assert ids == {"doc1", "doc2"}
    # doc1 = 1.0 (norm em A) + 0.0 (norm em B) = 1.0; doc2 = 0.0 + 1.0 = 1.0.
    scores = dict(fused)
    assert abs(scores["doc1"] - 1.0) < 1e-9
    assert abs(scores["doc2"] - 1.0) < 1e-9


def test_linear_fusion_pesos_reordenam():
    list_a = [("doc1", 10.0), ("doc2", 8.0)]
    list_b = [("doc2", 9.0), ("doc1", 7.0)]
    fused = LinearScoreFusion.fuse([list_a, list_b], weights=[10.0, 1.0])
    order = [item for item, _ in fused]
    assert order == ["doc1", "doc2"]  # 10*1.0 + 1*0.0 > 10*0.0 + 1*1.0
    scores = dict(fused)
    assert abs(scores["doc1"] - 10.0) < 1e-9
    assert abs(scores["doc2"] - 1.0) < 1e-9


def test_linear_fusion_deterministico_e_vazio():
    list_a = [("doc1", 5.0), ("doc2", 3.0)]
    list_b = [("doc2", 4.0)]
    one = LinearScoreFusion.fuse([list_a, list_b])
    two = LinearScoreFusion.fuse([list_a, list_b])
    assert one == two
    assert LinearScoreFusion.fuse([]) == []


# ── rank_lists: mesma entrada para os dois fusores ───────────────────────────

def test_rank_lists_devolve_duas_listas_do_pool_real(tmp_path):
    store = RAGFlowStore(tmp_path / "rag.db")
    _seed(store)
    fts, lexical = store.rank_lists("ProtocolAdapter refresh")
    # As duas listas carregam ids reais de chunks do store (pool fechado).
    ids = {cid for cid, _ in (fts + lexical)}
    assert ids, "rank_lists devolveu listas vazias"
    resolved = store.chunks_by_id([cid for cid, _ in (fts + lexical)])
    assert {c.doc_path for c in resolved} == {"docs/doc-a.md", "docs/doc-b.md"}


def test_rank_lists_deterministico_e_consistente_com_hybrid_search(tmp_path):
    store = RAGFlowStore(tmp_path / "rag.db")
    _seed(store)
    fts1, lex1 = store.rank_lists("ProtocolAdapter refresh")
    fts2, lex2 = store.rank_lists("ProtocolAdapter refresh")
    assert (fts1, lex1) == (fts2, lex2)
    # O topo fundido por RRF das listas cruas coincide com o hybrid_search.
    from hermes.platform.memory.ragflow_engine import ReciprocalRankFusion

    fused = ReciprocalRankFusion.fuse([fts1, lex1], k=60)
    top_id = fused[0][0] if fused else None
    top_chunk = store.chunks_by_id([top_id])[0] if top_id else None
    direct = store.hybrid_search("ProtocolAdapter refresh", limit=5)
    if top_chunk is not None and direct:
        assert top_chunk.chunk_id == direct[0].chunk_id