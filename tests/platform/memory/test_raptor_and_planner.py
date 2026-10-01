"""Contracts for RAPTOR hierarchical memory and the intent-aware retrieval planner.

Pins behavior, not snapshots:
- the tree is deterministic (same corpus → same clustering), levels are real
  (>1), parents aggregate children's leaves AND anchors (summaries are indexes
  into evidence, never free-floating claims);
- singleton clusters never become parents (no fake abstraction);
- retrieval spans levels: a gist query can hit an abstract, a term query a
  leaf; empty query/corpus → empty, no exception;
- memory candidates carry origin + anchors (governance bridge);
- planner: intent is table-driven and deterministic, unavailable routes are
  dropped and RECORDED (fail-closed, never simulated), execution is
  first-hit-wins with visible route errors, and every answer says how it was
  found.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes.platform.memory.document_understanding import parse_markdown
from hermes.platform.memory.raptor_memory import (
    RaptorStore,
    RaptorTreeBuilder,
    jaccard_similarity,
)
from hermes.platform.memory.retrieval_planner import (
    INTENT_CODE,
    INTENT_GLOBAL,
    INTENT_RELATIONAL,
    INTENT_TEMPORAL,
    classify_intent,
    execute_plan,
    plan_retrieval,
)
from hermes.platform.memory.semantic_chunker import SemanticChunker

DOC = """# Architecture

## Pagination

The repository layer uses keyset pagination for the sessions table.
Keyset pagination avoids the offset scan problem on large tables.

## Auth

Auth flows through the gateway token store. Tokens rotate per session.

## Storage

Storage uses SQLite WAL mode for the hot stores.
"""


def _chunks():
    tree = parse_markdown(DOC, doc_path="docs/arch.md")
    return SemanticChunker(max_chars=220).chunk_tree(tree, doc_id="arch")


def test_jaccard_basic():
    assert jaccard_similarity("alpha beta gamma", "alpha beta delta") > 0.0
    assert jaccard_similarity("alpha", "beta") == 0.0
    assert jaccard_similarity("", "x") == 0.0


def test_tree_levels_and_aggregation():
    nodes = RaptorTreeBuilder(max_level=3, cluster_threshold=0.08).build(_chunks())
    leaves = [n for n in nodes if n.level == 0]
    abstracts = [n for n in nodes if n.level > 0]
    assert len(leaves) == len(_chunks())
    assert abstracts, "similar chunks must cluster into at least one abstract"
    for parent in abstracts:
        assert len(parent.child_ids) >= 2, "singleton clusters must not become parents"
        assert len(parent.leaf_ids) >= 2
        # parents carry the FULL evidence trail of their subtree
        assert parent.provenance_anchors and all(
            a.startswith("[ref: docs/arch.md#L") for a in parent.provenance_anchors
        )


def test_tree_deterministic_clustering():
    chunks = _chunks()
    a = RaptorTreeBuilder(cluster_threshold=0.08).build(chunks)
    b = RaptorTreeBuilder(cluster_threshold=0.08).build(chunks)
    # node ids embed uuids; determinism claim is about STRUCTURE: same levels,
    # same membership sizes, same leaf sets per level
    sa = sorted((n.level, tuple(sorted(n.leaf_ids))) for n in a if n.level > 0)
    sb = sorted((n.level, tuple(sorted(n.leaf_ids))) for n in b if n.level > 0)
    assert sa == sb and sa


def test_custom_summarizer_seam():
    calls = []

    def spy(child_summaries):
        calls.append(len(child_summaries))
        return "SYNTHESIZED"

    nodes = RaptorTreeBuilder(cluster_threshold=0.08, summarizer=spy).build(_chunks())
    assert calls, "summarizer must be used for cluster nodes"
    assert any(n.summary == "SYNTHESIZED" for n in nodes if n.level > 0)


def test_store_roundtrip_and_multi_resolution_retrieve(tmp_path):
    store = RaptorStore(db_path=tmp_path / "raptor.db")
    nodes = RaptorTreeBuilder(cluster_threshold=0.08).build(_chunks())
    assert store.put_tree(nodes, corpus_id="arch") == len(nodes)
    assert store.max_level("arch") >= 1

    hits = store.retrieve("keyset pagination sessions table", "arch", k=6)
    assert hits
    assert hits[0]["level"] == 0  # a term-heavy query lands on the leaf
    assert hits[0]["provenance_anchors"]  # evidence trail on every hit

    gist = store.retrieve("pagination", "arch", k=6)
    assert any(h["level"] > 0 for h in gist) or len(store.all_nodes("arch")) == len(_chunks())

    assert store.retrieve("", "arch") == []
    assert store.retrieve("quantum", "missing-corpus") == []


def test_reingest_replaces_corpus(tmp_path):
    store = RaptorStore(db_path=tmp_path / "raptor.db")
    nodes = RaptorTreeBuilder(cluster_threshold=0.08).build(_chunks())
    store.put_tree(nodes, "c")
    store.put_tree(nodes[:2], "c")
    assert len(store.all_nodes("c")) == 2


def test_memory_candidates_carry_anchors(tmp_path):
    store = RaptorStore(db_path=tmp_path / "raptor.db")
    nodes = RaptorTreeBuilder(cluster_threshold=0.08).build(_chunks())
    store.put_tree(nodes, "arch")
    cands = store.as_memory_candidates("arch", level=1)
    assert cands
    for c in cands:
        assert c["metadata"]["origin"] == "raptor"
        assert c["metadata"]["anchors"], "a summary candidate must keep its evidence trail"


# ---------------------------------------------------------------- planner

def test_intent_classification_table():
    assert classify_intent("qual o resumo geral do projeto") == INTENT_GLOBAL
    assert classify_intent("what is the big picture of the system") == INTENT_GLOBAL
    assert classify_intent("quem depende do SessionDB") == INTENT_RELATIONAL
    assert classify_intent("qual a relação entre gateway e cron") == INTENT_RELATIONAL
    assert classify_intent("onde está implementado o chunker? arquivo .py") == INTENT_CODE
    assert classify_intent("quando mudamos a política de compressão") == INTENT_TEMPORAL
    assert classify_intent("blabla xyz") == "factual"
    assert classify_intent("") == "factual"


def test_plan_fail_closed_and_recorded():
    plan = plan_retrieval(
        "resumo geral da arquitetura",
        {"RAPTOR": True, "GRAPHRAG": False, "RAGFLOW": True},
        reason_for_missing={"GRAPHRAG": "client not configured"},
    )
    assert plan.intent == INTENT_GLOBAL
    assert plan.routes[0] == "RAPTOR"
    assert "GRAPHRAG" not in plan.routes
    # unavailable AND unwired routes are dropped with a reason — never simulated
    assert plan.skipped["GRAPHRAG"] == "client not configured"
    assert plan.skipped["RECONCILED_MEMORY"] == "not available"
    # the honest default: FACTUAL never routes first to a source it can't have
    f = plan_retrieval("o que é keyset pagination", {"RAGFLOW": True, "OKF": True})
    assert f.routes == ["OKF", "RAGFLOW"]


def test_execute_plan_first_hit_wins_with_visible_errors():
    plan = plan_retrieval(
        "resumo geral",
        {"RAPTOR": True, "RAGFLOW": True, "GRAPHRAG": True},
    )

    def boom(query):
        raise RuntimeError("graph down")

    execs = {
        "RAPTOR": lambda q: None,           # miss
        "GRAPHRAG": boom,                    # exception → recorded, continue
        "RAGFLOW": lambda q: {"answer": "doc hit"},
    }
    out = execute_plan(plan, execs)
    assert out["found"] is True
    assert out["route"] == "RAGFLOW"
    assert out["result"] == {"answer": "doc hit"}
    assert "GRAPHRAG" in out["route_errors"] and "graph down" in out["route_errors"]["GRAPHRAG"]
    assert out["plan"]["intent"] == INTENT_GLOBAL


def test_execute_plan_all_miss_is_honest():
    plan = plan_retrieval("resumo geral", {"RAPTOR": True})
    out = execute_plan(plan, {"RAPTOR": lambda q: None})
    assert out["found"] is False
    assert out["result"] is None
    # a planned route without an executor is a visible error, not a silent pass
    plan2 = plan_retrieval("resumo geral", {"RAPTOR": True, "GRAPHRAG": True})
    out2 = execute_plan(plan2, {"RAPTOR": lambda q: None})
    assert out2["found"] is False
    assert out2["route_errors"]["GRAPHRAG"] == "no executor wired"
