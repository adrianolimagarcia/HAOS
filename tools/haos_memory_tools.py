"""HAOS Memory Tools: Obsidian Vault, OKF Bundles & GraphRAG.

Registra as ferramentas de memória canônica e relacional do HAOS para o agente,
incluindo o Hybrid Router (OKF determinístico + RAG probabilístico local).
Funciona 100% offline em ambiente local.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_constants import get_hermes_home
from tools.registry import registry


def _get_obsidian_adapter():
    home = Path(get_hermes_home())
    vault = home / "vault"
    if not vault.exists():
        vault.mkdir(parents=True, exist_ok=True)
        adrs = vault / "adrs"
        adrs.mkdir(parents=True, exist_ok=True)
    from hermes.platform.memory.obsidian import ObsidianAdapter
    return ObsidianAdapter(str(vault))


def _get_graphrag_client():
    home = Path(get_hermes_home())
    gr_dir = home / "graphrag"
    if not gr_dir.exists():
        gr_dir.mkdir(parents=True, exist_ok=True)
    from hermes.platform.memory.graphrag import GraphRAGClient
    # store_path = store canônico (ADR-008) escrito pela stack A
    # (IncrementalGraphRAGUpdater -> GraphRAGStore); index_dir fica como
    # fallback CSV de demo/CI. available() é fail-closed por arquivo.
    from hermes.platform.context.memory.graphrag_store import default_graphrag_db_path
    return GraphRAGClient(
        index_dir=str(gr_dir),
        store_path=str(default_graphrag_db_path()),
    )


def _get_hybrid_router():
    home = Path(get_hermes_home())
    okf_dir = home / "okf"
    if not okf_dir.exists():
        okf_dir.mkdir(parents=True, exist_ok=True)
    gr_dir = home / "graphrag"
    from hermes.platform.memory.hybrid_router import HybridKnowledgeRouter
    return HybridKnowledgeRouter(okf_dir=okf_dir, graphrag_dir=gr_dir)


def obsidian_get_adr(adr_id: str) -> str:
    """Busca uma ADR (Architecture Decision Record) no Obsidian Vault."""
    try:
        adapter = _get_obsidian_adapter()
        if not adapter.available():
            return json.dumps({"error": "Obsidian Vault não disponível"})
        content = adapter.get_adr(adr_id)
        if content is None:
            return json.dumps({"found": False, "message": f"ADR '{adr_id}' não encontrada"})
        return json.dumps({"found": True, "adr_id": adr_id, "content": content})
    except Exception as e:
        return json.dumps({"error": str(e)})


def obsidian_save_note(title: str, content: str, folder: str = "") -> str:
    """Salva uma nota ou ADR no Obsidian Vault canônico."""
    try:
        home = Path(get_hermes_home())
        vault = home / "vault"
        target_dir = vault / folder if folder else vault
        target_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{title}.md" if not title.endswith(".md") else title
        note_path = target_dir / filename
        note_path.write_text(content, encoding="utf-8")
        return json.dumps({"success": True, "path": str(note_path)})
    except Exception as e:
        return json.dumps({"error": str(e)})


def graphrag_query(query: str, mode: str = "global") -> str:
    """Consulta o grafo de entidades e relacionamentos do GraphRAG."""
    try:
        client = _get_graphrag_client()
        if not client.available():
            return json.dumps({"error": "GraphRAG índice local não disponível"})
        if mode == "local":
            res = client.query_local(query)
        else:
            res = client.query_global(query)
        return json.dumps({"success": True, "results": res})
    except Exception as e:
        return json.dumps({"error": str(e)})


def haos_hybrid_memory_query(query: str, mode: str = "hybrid") -> str:
    """Consulta a memória híbrida (OKF determinístico primeiro + GraphRAG fallback local)."""
    try:
        router = _get_hybrid_router()
        res = router.query(query_str=query, mode=mode)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def haos_okf_save_document(
    title: str,
    content: str,
    doc_type: str = "concept",
    tags: Optional[str] = None,
    owner: str = "",
    folder: str = "",
) -> str:
    """Salva um documento canônico no formato OKF (YAML frontmatter + Markdown) localmente."""
    try:
        home = Path(get_hermes_home())
        okf_dir = home / "okf"
        from hermes.platform.memory.okf import OKFStore
        store = OKFStore(okf_dir)
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        doc = store.save_document(
            title=title,
            content=content,
            doc_type=doc_type,
            tags=tag_list,
            owner=owner,
            folder=folder,
        )
        return json.dumps({"success": True, "path": doc.relative_path, "title": doc.title})
    except Exception as e:
        return json.dumps({"error": str(e)})


registry.register(
    name="obsidian_get_adr",
    toolset="memory",
    schema={
        "name": "obsidian_get_adr",
        "description": "Recupera uma ADR (Architecture Decision Record) canônica do Obsidian Vault pelo ID (ex: 'ADR-001').",
        "parameters": {
            "type": "object",
            "properties": {
                "adr_id": {"type": "string", "description": "ID da ADR, ex: 'ADR-001'"}
            },
            "required": ["adr_id"],
        },
    },
    handler=lambda args, **kw: obsidian_get_adr(args.get("adr_id", "")),
)

registry.register(
    name="obsidian_save_note",
    toolset="memory",
    schema={
        "name": "obsidian_save_note",
        "description": "Grava uma nova nota de arquitetura, especificação ou ADR no Obsidian Vault canônico.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título da nota ou arquivo (ex: 'ADR-002-novo-modulo')"},
                "content": {"type": "string", "description": "Conteúdo Markdown da nota"},
                "folder": {"type": "string", "description": "Subpasta dentro do vault, padrão vazio ou 'adrs'"}
            },
            "required": ["title", "content"],
        },
    },
    handler=lambda args, **kw: obsidian_save_note(
        title=args.get("title", ""),
        content=args.get("content", ""),
        folder=args.get("folder", "")
    ),
)

registry.register(
    name="graphrag_query",
    toolset="memory",
    schema={
        "name": "graphrag_query",
        "description": "Consulta o grafo de conhecimento GraphRAG para encontrar entidades, componentes e seus relacionamentos.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Termo de busca ou entidade a consultar (ex: 'Kanban', 'HAOS')"},
                "mode": {"type": "string", "enum": ["global", "local"], "description": "Modo de consulta (global ou local)"}
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: graphrag_query(
        query=args.get("query", ""),
        mode=args.get("mode", "global")
    ),
)

registry.register(
    name="haos_hybrid_memory_query",
    toolset="memory",
    schema={
        "name": "haos_hybrid_memory_query",
        "description": "Consulta conhecimento canônico autoritativo (OKF local), documentos profundos via RAGFlow ou GraphRAG.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Termo, nome da técnica, especificação ou pergunta a consultar"},
                "mode": {"type": "string", "enum": ["hybrid", "okf", "rag"], "description": "Modo de busca (default: hybrid)"}
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: haos_hybrid_memory_query(
        query=args.get("query", ""),
        mode=args.get("mode", "hybrid")
    ),
)

registry.register(
    name="haos_okf_save_document",
    toolset="memory",
    schema={
        "name": "haos_okf_save_document",
        "description": "Salva uma especificação ou contrato canônico no formato OKF (Open Knowledge Format) localmente.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título oficial do conceito ou contrato"},
                "content": {"type": "string", "description": "Conteúdo Markdown e regras inegociáveis"},
                "doc_type": {"type": "string", "description": "Tipo do documento: 'metric', 'api-contract', 'runbook', 'architecture'"},
                "tags": {"type": "string", "description": "Tags separadas por vírgula (ex: 'revenue, kpi')"},
                "owner": {"type": "string", "description": "Time ou autor responsável"},
                "folder": {"type": "string", "description": "Subdiretório opcional"}
            },
            "required": ["title", "content"],
        },
    },
    handler=lambda args, **kw: haos_okf_save_document(
        title=args.get("title", ""),
        content=args.get("content", ""),
        doc_type=args.get("doc_type", "concept"),
        tags=args.get("tags"),
        owner=args.get("owner", ""),
        folder=args.get("folder", ""),
    ),
)
