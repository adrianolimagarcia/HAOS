"""Ingestão RAPTOR a partir do índice RAGFlow canônico (ponte read-only).

O RaptorStore persiste a árvore multi-resolução, mas nada no produto a
CONSTRUÍA: sem esta ponte, o ``_step_raptor`` do router sempre veria um
corpus vazio. Aqui os chunks já indexados do ``haos_rag_chunks`` (escritos
pela stack de ingestão do vault/OKF) viram ``KnowledgeChunk`` e alimentam o
``RaptorTreeBuilder`` — o store RAGFlow nunca é modificado.

``summary`` não é coluna do schema: derivamos cabeçalho + primeira frase, o
mesmo sinal que o builder usa para resumir clusters (extractive). A derivada
fica no ``metadata`` para auditoria.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from hermes.platform.memory.raptor_memory import RaptorStore, RaptorTreeBuilder
from hermes.platform.memory.semantic_chunker import KnowledgeChunk

_FIRST_SENTENCE = re.compile(r"^(.{1,240}?[.!?])(\s|$)")


def derive_summary(header_path: str, content: str) -> str:
    """Resumo curto determinístico: cabeçalho do chunk + primeira frase."""
    body = " ".join(content.split())
    m = _FIRST_SENTENCE.match(body)
    first = m.group(1) if m else body[:160]
    header = (header_path or "").strip()
    return f"{header}: {first}" if header else first


def chunks_from_ragflow(ragflow_store, limit_docs: Optional[int] = None) -> List[KnowledgeChunk]:
    """Lê TODOS os chunks do store RAGFlow e os mapeia para KnowledgeChunk.

    Colunas de ``haos_rag_chunks`` casam 1:1 com o dataclass, exceto
    ``summary`` (derivado) e ``breadcrumb`` (JSON). ``limit_docs`` limita por
    doc (não por chunk) — só para pilotos/CI; produção chama sem limite.
    """
    with ragflow_store._get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, doc_path, header_path, breadcrumb_json, content,
                   start_line, end_line, provenance_anchor
            FROM haos_rag_chunks
            ORDER BY doc_path, start_line
            """
        ).fetchall()
        if limit_docs is not None:
            docs = {r["doc_path"] for r in rows}
            keep = sorted(docs)[:limit_docs]
            keep_set = set(keep)
            rows = [r for r in rows if r["doc_path"] in keep_set]

    chunks: List[KnowledgeChunk] = []
    for r in rows:
        try:
            breadcrumb = json.loads(r["breadcrumb_json"] or "[]")
        except ValueError:
            breadcrumb = []
        chunks.append(
            KnowledgeChunk(
                chunk_id=r["id"],
                doc_path=r["doc_path"],
                header_path=r["header_path"],
                breadcrumb=list(breadcrumb),
                content=r["content"],
                summary=derive_summary(r["header_path"], r["content"]),
                start_line=int(r["start_line"]),
                end_line=int(r["end_line"]),
                provenance_anchor=r["provenance_anchor"],
            )
        )
    return chunks


def build_from_ragflow(
    ragflow_store,
    raptor_store: Optional[RaptorStore] = None,
    corpus_id: str = "default",
    *,
    builder: Optional[RaptorTreeBuilder] = None,
    limit_docs: Optional[int] = None,
) -> Dict[str, object]:
    """Pipeline completo: lê RAGFlow → constrói a árvore → persiste no RaptorStore.

    Idempotente por corpus: ``put_tree`` substitui a árvore inteira do
    ``corpus_id`` (nunca mistura versões). Retorna um relatório com contagens
    por nível para o chamador (CLI/fabric) reportar — nada de log silencioso.
    """
    store = raptor_store or RaptorStore()
    chunks = chunks_from_ragflow(ragflow_store, limit_docs=limit_docs)
    if not chunks:
        return {"chunks": 0, "nodes": 0, "levels": {}, "max_level": 0,
                "corpus_id": corpus_id, "db": str(store.db_path)}

    tree = (builder or RaptorTreeBuilder()).build(chunks)
    written = store.put_tree(tree, corpus_id)
    by_level: Dict[int, int] = {}
    for n in tree:
        by_level[n.level] = by_level.get(n.level, 0) + 1
    return {
        "chunks": len(chunks),
        "nodes": written,
        "levels": {str(k): v for k, v in sorted(by_level.items())},
        "max_level": store.max_level(corpus_id),
        "corpus_id": corpus_id,
        "db": str(store.db_path),
    }
