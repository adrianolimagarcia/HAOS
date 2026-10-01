"""Hybrid Knowledge Router (OKF + MemoryReconciler + RAG/GraphRAG/RAPTOR) for HAOS.

Combines:
1. Reconciled active declarative memories (Mem0 mutation & conflict resolution: ADD/UPDATE/SUPERSEDE/NOOP).
2. Deterministic, offline-first OKF canonical bundles.
3. Probabilistic relational/vector GraphRAG retrieval.
4. (opt-in) Intent-aware routing via ``retrieval_planner`` + RAPTOR multi-level tree.

Works 100% offline with zero cloud dependency.

``query()`` keeps the ORIGINAL fixed cascade by default (byte-compatible
behavior for every existing consumer). ``use_planner=True`` routes by query
intent instead — global questions try the RAPTOR abstraction first, factual
ones keep the memory-first cascade — and the answer then carries the plan
that produced it (``intent``/``plan`` keys). The planner never invents a
source: routes without a wired backend are dropped and recorded as skipped.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.memory.okf import OKFStore
from hermes.platform.memory.graphrag import GraphRAGClient
from hermes.platform.memory.reconciler import MemoryReconciler, ReconciliationResult, MemoryRecord
from hermes.platform.memory.ragflow_engine import RAGFlowStore, DocumentChunk
from hermes.platform.memory.retrieval_planner import (
    execute_plan,
    plan_retrieval,
)

logger = logging.getLogger("haos.memory.hybrid_router")


class HybridKnowledgeRouter:
    """Intelligent router directing queries across Reconciled Memory, OKF, RAGFlow, GraphRAG and RAPTOR."""

    def __init__(
        self,
        okf_dir: Path,
        graphrag_dir: Optional[Path] = None,
        reconciler_db_path: Optional[Path] = None,
        reconciler: Optional[MemoryReconciler] = None,
        ragflow_store: Optional[RAGFlowStore] = None,
        raptor_store=None,
        raptor_corpus_id: str = "default",
        use_planner: bool = False,
    ):
        self.okf_store = OKFStore(okf_dir)
        self.graphrag_client = (
            GraphRAGClient(index_dir=str(graphrag_dir)) if graphrag_dir else None
        )
        self.reconciler = reconciler or MemoryReconciler(db_path=reconciler_db_path)
        self.ragflow_store = ragflow_store or RAGFlowStore()
        self.raptor_store = raptor_store
        self.raptor_corpus_id = raptor_corpus_id
        self.use_planner = use_planner

    def reconcile_memory(
        self,
        *,
        topic: str,
        content: str,
        category: str = "general",
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReconciliationResult:
        """Mutate and reconcile a candidate fact with conflict resolution (ADD, UPDATE, SUPERSEDE, NOOP)."""
        return self.reconciler.reconcile(
            topic=topic,
            content=content,
            category=category,
            confidence=confidence,
            metadata=metadata,
        )

    def get_active_memories(
        self,
        *,
        topic: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> List[MemoryRecord]:
        """Retrieve active facts only (superseded facts are never returned)."""
        return self.reconciler.get_active_memories(topic=topic, category=category, limit=limit)

    # ------------------------------------------------------------------ steps
    # One implementation per source, shared by the fixed cascade and the
    # planner path. Each returns a result dict on hit, None on miss.

    def _step_reconciled(self, query_str: str) -> Optional[Dict[str, Any]]:
        clean_q = query_str.strip().lower()
        active_mems = self.reconciler.get_active_memories(topic=clean_q)
        if not active_mems:
            all_active = self.reconciler.get_active_memories(limit=100)
            active_mems = [
                m for m in all_active
                if clean_q in m.topic.lower() or clean_q in m.content.lower() or m.topic.lower() in clean_q
            ]
        if not active_mems:
            return None
        top_mem = active_mems[0]
        return {
            "source": "RECONCILED_MEMORY",
            "deterministic": True,
            "found": True,
            "memory_id": top_mem.id,
            "topic": top_mem.topic,
            "status": top_mem.status,
            "content": f"[SOURCE: RECONCILED ACTIVE MEMORY ({top_mem.topic})]\n{top_mem.content}",
        }

    def _step_okf(self, query_str: str) -> Optional[Dict[str, Any]]:
        okf_doc = self.okf_store.find_deterministic(query_str)
        if not okf_doc:
            return None
        return {
            "source": "OKF_CANONICAL",
            "deterministic": True,
            "found": True,
            "doc": okf_doc.to_dict(),
            "content": f"[SOURCE: CANONICAL KNOWLEDGE (OKF)]\nTitle: {okf_doc.title}\n\n{okf_doc.body}",
        }

    def _step_ragflow(self, query_str: str,
                     retrieval_budget: Optional[int] = None) -> Optional[Dict[str, Any]]:
        if not self.ragflow_store:
            return None
        try:
            rag_chunks = self.ragflow_store.hybrid_search(
                query_str, limit=3, max_candidates=retrieval_budget,
            )
        except Exception as exc:
            logger.warning("RAGFlow search failed: %s", exc)
            return None
        if not rag_chunks:
            return None
        formatted_content = "\n\n---\n\n".join(c.formatted_for_llm() for c in rag_chunks)
        top_c = rag_chunks[0]
        return {
            "source": "RAGFLOW_HYBRID",
            "deterministic": True,
            "found": True,
            "doc_path": top_c.doc_path,
            "provenance_anchor": top_c.provenance_anchor,
            "chunks": [c.to_dict() for c in rag_chunks],
            "content": f"[SOURCE: RAGFLOW DEEP DOCUMENT RETRIEVAL]\n{formatted_content}",
        }

    def _step_raptor(self, query_str: str) -> Optional[Dict[str, Any]]:
        if self.raptor_store is None:
            return None
        hits = self.raptor_store.retrieve(query_str, self.raptor_corpus_id, k=3)
        if not hits:
            return None
        top = hits[0]
        return {
            "source": "RAPTOR_TREE",
            "deterministic": True,
            "found": True,
            "level": top["level"],
            "provenance_anchors": top["provenance_anchors"],
            "hits": hits,
            "content": (
                f"[SOURCE: RAPTOR MULTI-RESOLUTION (level {top['level']})]\n"
                + "\n\n".join(h["summary"] for h in hits)
            ),
        }

    def _step_graphrag(self, query_str: str, mode: str) -> Optional[Dict[str, Any]]:
        if mode not in ("hybrid", "rag"):
            return None
        if not (self.graphrag_client and self.graphrag_client.available()):
            return None
        try:
            rag_results = self.graphrag_client.query_global(query_str)
        except Exception as exc:
            logger.warning("GraphRAG query failed: %s", exc)
            return None
        if not rag_results:
            return None
        return {
            "source": "RAG_PROBABILISTIC",
            "deterministic": False,
            "found": True,
            "results": rag_results,
            "content": f"[SOURCE: PROBABILISTIC SEARCH (GraphRAG)]\n{rag_results}",
        }

    def _step_okf_broad(self, query_str: str) -> Optional[Dict[str, Any]]:
        broader_matches = self.okf_store.search_all(query_str)
        if not broader_matches:
            return None
        top_match = broader_matches[0]
        return {
            "source": "OKF_BROAD_MATCH",
            "deterministic": True,
            "found": True,
            "doc": top_match.to_dict(),
            "content": f"[SOURCE: CANONICAL KNOWLEDGE (OKF - Fuzzy)]\nTitle: {top_match.title}\n\n{top_match.body}",
        }

    # ------------------------------------------------------------------ query

    def query(self, query_str: str, mode: str = "hybrid",
              retrieval_budget: "Optional[int]" = None,
              use_planner: "Optional[bool]" = None) -> Dict[str, Any]:
        """Perform routed query.

        Fixed cascade (default, unchanged behavior):
        1. Reconciled Memory → 2. OKF deterministic → 3. RAGFlow hybrid →
        4. GraphRAG (mode hybrid/rag) → 5. OKF broad fallback → NONE.

        Planner path (``use_planner=True`` or constructed with it): classify
        query intent, order the AVAILABLE sources by intent, first hit wins,
        then OKF broad fallback. The result carries ``intent`` + ``plan`` so
        every answer says how it was found.

        ``retrieval_budget`` (P6 — kill-switch por orçamento) é repassado ao
        RAGFlowStore.hybrid_search como ``max_candidates``: limita a avaliação
        de candidatos na passada léxica; o disparo fica em
        ``ragflow_store.last_search_budget``. ``None`` mantém o comportamento
        original.
        """
        planner_on = self.use_planner if use_planner is None else bool(use_planner)
        if planner_on:
            return self._query_planned(query_str, mode=mode,
                                       retrieval_budget=retrieval_budget)

        for step in (
            lambda: self._step_reconciled(query_str),
            lambda: self._step_okf(query_str),
            lambda: self._step_ragflow(query_str, retrieval_budget),
            lambda: self._step_graphrag(query_str, mode),
            lambda: self._step_okf_broad(query_str),
        ):
            result = step()
            if result is not None:
                return result

        return {
            "source": "NONE",
            "deterministic": True,
            "found": False,
            "message": f"No authoritative or probabilistic knowledge found for query: {query_str!r}",
        }

    def _query_planned(self, query_str: str, *, mode: str,
                       retrieval_budget: Optional[int]) -> Dict[str, Any]:
        available = {
            "RECONCILED_MEMORY": True,
            "OKF": True,
            "RAGFLOW": self.ragflow_store is not None,
            "RAPTOR": self.raptor_store is not None,
            "GRAPHRAG": bool(self.graphrag_client and self.graphrag_client.available()),
        }
        reasons = {
            "RAPTOR": "no raptor_store wired",
            "GRAPHRAG": "graphrag client unavailable",
            "RAGFLOW": "no ragflow_store wired",
        }
        plan = plan_retrieval(query_str, available, reason_for_missing=reasons)
        executors = {
            "RECONCILED_MEMORY": self._step_reconciled,
            "OKF": self._step_okf,
            "RAGFLOW": lambda q: self._step_ragflow(q, retrieval_budget),
            "RAPTOR": self._step_raptor,
        }
        if mode in ("hybrid", "rag"):
            executors["GRAPHRAG"] = lambda q: self._step_graphrag(q, mode)

        out = execute_plan(plan, executors)
        if out["found"]:
            result = dict(out["result"])
            result["intent"] = out["intent"]
            result["plan"] = out["plan"]
            if out["route_errors"]:
                result["route_errors"] = out["route_errors"]
            return result

        broad = self._step_okf_broad(query_str)
        if broad is not None:
            broad["intent"] = out["intent"]
            broad["plan"] = out["plan"]
            return broad

        return {
            "source": "NONE",
            "deterministic": True,
            "found": False,
            "intent": out["intent"],
            "plan": out["plan"],
            "route_errors": out["route_errors"],
            "message": f"No authoritative or probabilistic knowledge found for query: {query_str!r}",
        }
