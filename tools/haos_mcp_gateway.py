"""HAOS MCP Gateway Model Tools.

Expõe ferramentas no Hermes para inspeção e controle do agregador local de MCPs.
Toolset: `mcp_gateway`
"""

import asyncio
import json
from typing import Any, Dict, Optional

from tools.registry import registry
from hermes.platform.mcp.aggregator import get_local_aggregator


def mcp_gateway_status() -> str:
    """Retorna o status consolidado de todos os servidores MCP e ferramentas agregadas."""
    aggregator = get_local_aggregator()
    status = aggregator.get_status()
    return json.dumps(status, indent=2)


def mcp_gateway_list_tools() -> str:
    """Lista todas as ferramentas agregadas com seus respectivos namespaces e descrições."""
    aggregator = get_local_aggregator()
    tools = aggregator.list_tools()
    return json.dumps({"count": len(tools), "tools": tools}, indent=2)


def mcp_gateway_call(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    """Executa uma ferramenta federada passando pelo gateway local (roteamento e circuit breaker)."""
    aggregator = get_local_aggregator()
    args = arguments or {}

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                res = pool.submit(
                    lambda: asyncio.run(aggregator.call_tool(tool_name, args))
                ).result()
        else:
            res = asyncio.run(aggregator.call_tool(tool_name, args))
        return json.dumps(res, indent=2)
    except Exception as exc:
        return json.dumps({"isError": True, "error": str(exc)})


registry.register(
    name="mcp_gateway_status",
    toolset="mcp_gateway",
    schema={
        "name": "mcp_gateway_status",
        "description": "Retorna a saúde, contagem de falhas, latência e estatísticas de todos os servidores MCP federados no gateway.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=lambda args, **kw: mcp_gateway_status(),
)

registry.register(
    name="mcp_gateway_list_tools",
    toolset="mcp_gateway",
    schema={
        "name": "mcp_gateway_list_tools",
        "description": "Retorna o catálogo unificado de ferramentas disponíveis através do MCP Gateway, com namespacing automático.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=lambda args, **kw: mcp_gateway_list_tools(),
)

registry.register(
    name="mcp_gateway_call",
    toolset="mcp_gateway",
    schema={
        "name": "mcp_gateway_call",
        "description": "Chama uma ferramenta federada através do MCP Gateway usando o nome qualificado com namespace (<server>_<tool>).",
        "parameters": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Nome da ferramenta com namespace (ex: 'sqlite_query' ou 'banking_get_balance')",
                },
                "arguments": {
                    "type": "object",
                    "description": "Dicionário de argumentos da chamada para a ferramenta",
                },
            },
            "required": ["tool_name"],
        },
    },
    handler=lambda args, **kw: mcp_gateway_call(
        tool_name=args.get("tool_name", ""),
        arguments=args.get("arguments") or {},
    ),
)
