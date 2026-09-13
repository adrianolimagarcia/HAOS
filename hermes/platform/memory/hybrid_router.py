"""Hybrid Knowledge Router (OKF + MemoryReconciler + RAG/GraphRAG) for HAOS.

Combines:
1. Reconciled active declarative memories (Mem0 mutation & conflict resolution: ADD/UPDATE/SUPERSEDE/NOOP).
2. Deterministic, offline-first OKF canonical bundles.
3. Probabilistic relational/vector GraphRAG retrieval.
Works 100% offline with zero cloud dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.memory.okf import OKFStore
from hermes.platform.memory.graphrag import GraphRAGClient
from hermes.platform.memory.reconciler import MemoryReconciler, ReconciliationResult, MemoryRecord
from hermes.platform.memory.ragflow_engine import RAGFlowStore, DocumentChunk


class HybridKnowledgeRouter:
    """Intelligent router directing queries across Reconciled Memory, OKF, RAGFlow, and GraphRAG."""

    def __init__(
        self,
        okf_dir: Path,
        graphrag_dir: Optional[Path] = None,
        reconciler_db_path: Optional[Path] = None,
        reconciler: Optional[MemoryReconciler] = None,
        ragflow_store: Optional[RAGFlowStore] = None,
    ):
        self.okf_store = OKFStore(okf_dir)
        self.graphrag_client = (
            GraphRAGClient(index_dir=str(graphrag_dir)) if graphrag_dir else None
        )
        self.reconciler = reconciler or MemoryReconciler(db_path=reconciler_db_path)
        self.ragflow_store = ragflow_store or RAGFlowStore()

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

    def query(self, query_str: str, mode: str = "hybrid") -> Dict[str, Any]:
        """Perform routed query.

        1. Reconciled Memory path: Match active memories for the topic/query.
        2. Deterministic path: Check OKF store.
        3. Probabilistic path: Fallback to GraphRAG if no deterministic match is found.
        4. OKF broad fuzzy search fallback.
        """
        clean_q = query_str.strip().lower()

        # Step 0: Check active reconciled memories
        active_mems = self.reconciler.get_active_memories(topic=clean_q)
        if not active_mems:
            # Check for substring match in active memories content or topic
            all_active = self.reconciler.get_active_memories(limit=100)
            active_mems = [
                m for m in all_active
                if clean_q in m.topic.lower() or clean_q in m.content.lower() or m.topic.lower() in clean_q
            ]

        if active_mems:
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

        # Step 1: Deterministic lookup via OKF
        okf_doc = self.okf_store.find_deterministic(query_str)
        if okf_doc:
            return {
                "source": "OKF_CANONICAL",
                "deterministic": True,
                "found": True,
                "doc": okf_doc.to_dict(),
                "content": f"[SOURCE: CANONICAL KNOWLEDGE (OKF)]\nTitle: {okf_doc.title}\n\n{okf_doc.body}",
            }

        # Step 2: Document index retrieval via RAGFlow (Breadcrumbs + RRF)
        if self.ragflow_store:
            try:
                rag_chunks = self.ragflow_store.hybrid_search(query_str, limit=3)
                if rag_chunks:
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
            except Exception as exc:
                logger.warning("RAGFlow search failed: %s", exc)

        # Step 3: If mode is hybrid or rag, attempt GraphRAG
        if mode in ("hybrid", "rag") and self.graphrag_client and self.graphrag_client.available():
            try:
                rag_results = self.graphrag_client.query_global(query_str)
                return {
                    "source": "RAG_PROBABILISTIC",
                    "deterministic": False,
                    "found": bool(rag_results),
                    "results": rag_results,
                    "content": f"[SOURCE: PROBABILISTIC SEARCH (GraphRAG)]\n{rag_results}",
                }
            except Exception as exc:
                return {
                    "source": "RAG_ERROR",
                    "deterministic": False,
                    "found": False,
                    "error": str(exc),
                }

        # Step 3: Broader search in OKF as fallback before failing
        broader_matches = self.okf_store.search_all(query_str)
        if broader_matches:
            top_match = broader_matches[0]
            return {
                "source": "OKF_BROAD_MATCH",
                "deterministic": True,
                "found": True,
                "doc": top_match.to_dict(),
                "content": f"[SOURCE: CANONICAL KNOWLEDGE (OKF - Fuzzy)]\nTitle: {top_match.title}\n\n{top_match.body}",
            }

        return {
            "source": "NONE",
            "deterministic": True,
            "found": False,
            "message": f"No authoritative or probabilistic knowledge found for query: {query_str!r}",
        }
