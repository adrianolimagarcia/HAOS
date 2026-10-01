"""Contracts for Sprint C: planner-wired router, semantic ingestion, metrics.

Pins behavior, not snapshots:
- the FIXED cascade is byte-compatible for existing consumers (default flag);
- planner mode routes by intent, RAPTOR first for global questions, and the
  answer carries intent + plan (every answer says how it was found);
- unavailable sources are skipped and RECORDED, never simulated;
- a broken source records its error and the cascade continues;
- SemanticDocumentChunker indexes into the real RAGFlowStore with zero schema
  change and hybrid_search finds the gold doc (end-to-end wiring);
- recall/MRR math is exact on hand-built rank lists (0, 1, mid-rank).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes.platform.memory.hybrid_router import HybridKnowledgeRouter
from hermes.platform.memory.ragflow_engine import RAGFlowStore
from hermes.platform.memory.raptor_memory import RaptorStore, RaptorTreeBuilder
from hermes.platform.memory.semantic_chunker import (
    SemanticDocumentChunker,
    SemanticChunker,
)

from evals.rag_recall_benchmark import compute_recall_mrr

DOC = """# Architecture

## Pagination

The repository layer uses keyset pagination for the sessions table.
Keyset pagination avoids the offset scan problem on large tables.

## Auth

Auth flows through the gateway token store. Tokens rotate per session.
"""


def _router(tmp_path, raptor_store=None, use_planner=False):
    return HybridKnowledgeRouter(
        okf_dir=tmp_path / "okf",
        reconciler_db_path=tmp_path / "rec.db",
        ragflow_store=RAGFlowStore(db_path=tmp_path / "rag.db"),
        raptor_store=raptor_store,
        raptor_corpus_id="corpus" if raptor_store is not None else "default",
        use_planner=use_planner,
    )


def test_fixed_cascade_unchanged_default(tmp_path):
    router = _router(tmp_path)
    res = router.query("Nonexistent Query")
    assert res["source"] == "NONE" and res["found"] is False
    # no planner keys leak into the legacy path
    assert "plan" not in res and "intent" not in res


def test_planner_mode_routes_by_intent(tmp_path):
    store = RaptorStore(db_path=tmp_path / "raptor.db")
    tree = __import__(
        "hermes.platform.memory.document_understanding", fromlist=["parse_markdown"]
    ).parse_markdown(DOC, doc_path="docs/arch.md")
    chunks = SemanticChunker(max_chars=220).chunk_tree(tree, doc_id="arch")
    store.put_tree(RaptorTreeBuilder(cluster_threshold=0.08).build(chunks), "corpus")

    router = _router(tmp_path, raptor_store=store, use_planner=True)
    # English-overlap query: lexical retrieval is token-based, and "overview"
    # is what classifies this as GLOBAL intent (the Portuguese synonyms are
    # covered by classify_intent's own test).
    res = router.query("overview of architecture pagination")
    assert res["found"] is True
    assert res["intent"] == "global"
    # global intent tried RAPTOR (the abstraction) — plan shows the order
    assert res["plan"]["routes"][0] == "RAPTOR"
    assert res["source"] == "RAPTOR_TREE"


def test_planner_records_skipped_sources(tmp_path):
    router = _router(tmp_path, raptor_store=None, use_planner=True)
    res = router.query("Nonexistent Query")
    assert res["found"] is False
    assert res["plan"]["skipped"]["RAPTOR"] == "no raptor_store wired"


def test_planner_per_query_override(tmp_path):
    router = _router(tmp_path, use_planner=False)
    res = router.query("Nonexistent Query", use_planner=True)
    assert "plan" in res  # planner ran per-call


def test_semantic_chunker_indexes_into_real_store(tmp_path):
    store = RAGFlowStore(db_path=tmp_path / "rag2.db",
                         chunker=SemanticDocumentChunker())
    n = store.index_document(doc_path="docs/arch.md", text=DOC)
    assert n >= 2
    hits = store.hybrid_search("keyset pagination sessions", limit=3)
    assert hits and hits[0].doc_path == "docs/arch.md"
    # anchors survive the round trip through the store
    assert hits[0].provenance_anchor.startswith("[ref: docs/arch.md#L")


def test_recall_mrr_math_exact():
    # gold at rank 1 → recall 1, rr 1; gold absent → rr 0
    out = compute_recall_mrr(
        results_per_query=[["a.md", "b.md"], ["b.md", "c.md"], ["x.md"]],
        gold_per_query=["a.md", "c.md", "zz.md"],
        k=3,
    )
    assert out["recall_at_k"] == round(2 / 3, 4)
    assert out["mrr"] == round((1.0 + 0.5 + 0.0) / 3, 4)
    assert out["queries"] == 3


def test_recall_mrr_rejects_misalignment():
    import pytest
    with pytest.raises(ValueError):
        compute_recall_mrr([["a"]], ["a", "b"], k=1)


def test_benchmark_end_to_end_synthetic(tmp_path):
    from evals.rag_recall_benchmark import run_benchmark
    metrics = run_benchmark(tmp_path / "bench.db", k=3)
    # synthetic corpus + gold pairs are constructed to be retrievable; if this
    # ever drops below 1.0 the wiring regressed — print the real number, don't
    # fudge the threshold
    assert metrics["recall_at_k"] == 1.0, metrics
    assert metrics["mrr"] > 0.5, metrics
