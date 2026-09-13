"""Sincroniza uma nota do vault para os stores derivados (DeepDoc/RAG + GraphRAG).

A escrita no vault canônico (``obsidian_vault``) só grava o ``.md``. Os stores
derivados que o agente consulta — ``memory/ragflow.db`` (busca ``haos-edge doc
search``) e ``memory/graphrag.db`` (``graphrag_query``) — precisavam esperar a
rotina noturna para refletir a nota nova. Estas funções espelham UMA nota nos
dois stores na hora da escrita, então a sincronização passa a ser imediata e a
rotina noturna vira a passada de reconciliação/rebuild completo.

Ambas as escritas são upsert idempotente: reescrever a mesma nota não duplica
chunk nem entidade (o ``RAGFlowStore`` remove por ``doc_path`` antes de inserir;
o ``IncrementalGraphRAGUpdater`` faz upsert por PK no write-through).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict


def deepdoc_index_note(home: Path, note_path: Path) -> int:
    """Indexa um único arquivo no store DeepDoc (FTS5 + breadcrumbs)."""
    from hermes.platform.memory.ragflow_engine import RAGFlowStore

    store = RAGFlowStore(db_path=home / "memory" / "ragflow.db")
    return store.index_file(note_path)


def graphrag_upsert_note(home: Path, uri: str, title: str, content: str) -> Dict[str, int]:
    """Espelha uma nota no store canônico GraphRAG via write-through do updater."""
    from hermes.platform.context.memory.events import (
        KnowledgeEvent,
        KnowledgeEventType,
    )
    from hermes.platform.context.memory.graphrag import GraphRAGAdapter
    from hermes.platform.context.memory.graphrag_store import GraphRAGStore
    from hermes.platform.context.memory.incremental_graphrag import (
        IncrementalGraphRAGUpdater,
    )

    store_path = home / "memory" / "graphrag.db"
    graph = GraphRAGAdapter()
    with GraphRAGStore(db_path=store_path) as store:
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        updater.process_event(
            KnowledgeEvent.create(
                event_type=KnowledgeEventType.NOTE_CREATED,
                uri=uri,
                title=title,
                content=content,
            )
        )
        return store.counts()


def sync_note(
    home: Path,
    note_path: Path,
    relative_path: str,
    title: str,
    content: str,
) -> Dict:
    """Espelha uma nota recém-escrita nos dois stores derivados de uma vez."""
    chunks = deepdoc_index_note(home, note_path)
    counts = graphrag_upsert_note(
        home,
        uri=f"obsidian://{relative_path}",
        title=title,
        content=content,
    )
    return {"deepdoc_chunks": chunks, **counts}
