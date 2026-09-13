"""Expose the persisted Codebase Wiki as federated MCP tools.

Fase 3 ``--mcp``: register the wiki as a *local server* on the HAOS
``LocalMCPAggregator`` so its query tools appear in the federated catalog
(namespaced ``codebase-wiki_*``) and the kernel/gateway can delegate to them —
no new core tool, no external MCP host, fully offline. The dispatcher reads
``<out_dir>/graph.json`` through the pure ``query`` layer on every call, so the
index stays the single source of truth and re-running ``hermes codebase-wiki``
immediately refreshes what the tools answer.

Registration is idempotent per aggregator instance; ``register_wiki_server``
returns the federated tool names that were added.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from . import query

# Tool schemas (MCP inputSchema shape, matching LocalMCPAggregator expectations).
WIKI_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "wiki_status",
        "description": "Resumo do mapa de código (arquivos, nós, arestas, god nodes).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "wiki_search",
        "description": "Busca nós do mapa cujo id/label contém o termo "
        "(módulos, classes, funções, docs).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Substring a procurar em ids/labels."},
                "limit": {"type": "integer", "description": "Máx. de resultados (default 20)."},
            },
            "required": ["term"],
        },
    },
    {
        "name": "wiki_edges",
        "description": "Arestas (importa/chama/herda/cita) que tocam um nó — "
        "quem usa e o que o nó usa.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "Id do nó, ex. tools.registry::dispatch.",
                },
                "direction": {"type": "string", "description": "out | in | both (default both)."},
                "limit": {"type": "integer", "description": "Máx. de arestas (default 40)."},
            },
            "required": ["node"],
        },
    },
    {
        "name": "wiki_path",
        "description": "Caminho mais curto entre dois nós (para perguntas 'o que conecta X a Y?').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Id do nó de partida."},
                "end": {"type": "string", "description": "Id do nó de chegada."},
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "wiki_god_nodes",
        "description": "Conceitos mais conectados do mapa (hubs de dependência).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Quantos listar (default 10)."},
            },
        },
    },
]


def _text_result(text: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _error_result(message: str) -> Dict[str, Any]:
    return {"isError": True, "content": [{"type": "text", "text": message}]}


async def wiki_dispatcher(
    graph_path: Path,
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """Async dispatcher body: answers one namespaced wiki tool from the persisted graph."""
    try:
        payload = query.load_graph_json(graph_path)
    except FileNotFoundError as exc:
        return _error_result(f"Mapa indisponível: {exc} — rode `hermes codebase-wiki` primeiro.")
    args = arguments or {}
    if tool_name == "wiki_status":
        return _text_result(str(query.summarize(payload)))
    if tool_name == "wiki_search":
        hits = query.search_nodes(
            payload, str(args.get("term", "")), limit=int(args.get("limit", 20))
        )
        if not hits:
            return _text_result("Nenhum nó encontrado.")
        lines = [f"{len(hits)} nó(s):"]
        for n in hits:
            lines.append(f"- {query.node_line(n)}")
        return _text_result("\n".join(lines))
    if tool_name == "wiki_edges":
        node = str(args.get("node", ""))
        if node not in {n["id"] for n in payload.get("nodes", [])}:
            return _error_result(f"Nó desconhecido: {node} (use wiki_search para achar o id).")
        return _text_result(
            query.format_neighbors(
                payload, node,
                direction=str(args.get("direction", "both")),
                limit=int(args.get("limit", 40)),
            )
        )
    if tool_name == "wiki_path":
        chain = query.shortest_path(payload, str(args.get("start", "")), str(args.get("end", "")))
        if chain is None:
            return _text_result("Sem caminho entre os nós (grafos desconexos ou nó desconhecido).")
        lines = [f"Caminho ({len(chain)-1} aresta(s)):"]
        nodes = query.node_index(payload)
        for i, nid in enumerate(chain):
            arrow = "--" if i == len(chain) - 1 else "->"
            lines.append(f"`{nid}` {arrow}")
            loc = nodes.get(nid, {}).get("source_file", "")
            if loc:
                lines[-1] += f" ({loc})"
        return _text_result("\n".join(lines))
    if tool_name == "wiki_god_nodes":
        gods = query.god_nodes(payload, top=int(args.get("limit", 10)))
        lines = [f"God nodes (grau):"]
        for g in gods:
            lines.append(f"- {g['degree']}: `{g['id']}` ({g['kind']})")
        return _text_result("\n".join(lines))
    return _error_result(f"Ferramenta wiki desconhecida: {tool_name}")


def register_wiki_server(
    aggregator: Any,
    graph_path: Path,
    server_name: str = "codebase-wiki",
) -> List[str]:
    """Register the wiki query tools on a LocalMCPAggregator instance.

    Returns the federated names added (``<server>_<tool>``). Dispatcher reads
    ``graph_path`` (``<out>/graph.json``) on demand. Safe to call again after
    re-indexing — the dispatcher always reads the freshest persisted graph.
    """
    # Late imports: hermes/platform/mcp must not become a hard dep of the pure
    # codebase pipeline — only MCP registration needs it.
    graph_path = Path(graph_path)

    async def dispatcher(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await wiki_dispatcher(graph_path, tool_name, arguments)

    aggregator.register_server_tools(server_name, WIKI_TOOLS)
    aggregator.register_dispatcher(server_name, dispatcher)
    return [f"{server_name}_{t['name']}" for t in WIKI_TOOLS]
