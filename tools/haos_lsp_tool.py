"""HAOS LSP Query Tool: Semantic code intelligence for autonomous agents.

Provides precise IDE-grade navigation via Language Server Protocol (LSP):
- definition: Go to symbol definition (file, line, column).
- references: Find all references/usages of a symbol across the project.
- hover: Get type signatures and documentation for a symbol.
- implementation: Find concrete implementations of interfaces or abstract methods.

Backed by agent.lsp Language Server daemons (Pyright, Rust Analyzer, gopls, tsserver).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.lsp import get_service
from agent.lsp.client import file_uri, uri_to_path

logger = logging.getLogger("tools.lsp")


def lsp_query_tool(
    action: str,
    path: str,
    line: int,
    character: int = 0,
    task_id: str = "default",
) -> str:
    """Execute semantic LSP query on a source file at specified line and character.

    Args:
        action: 'definition' | 'references' | 'hover' | 'implementation'
        path: File path (relative or absolute)
        line: 1-based line number
        character: 0-based character column offset
        task_id: Task identifier for environment resolution
    """
    resolved_path = str(Path(os.path.expanduser(path)).resolve())
    if not os.path.exists(resolved_path):
        return json.dumps({"success": False, "error": f"File not found: {path}"}, ensure_ascii=False)

    service = get_service()
    if service is None or not service.is_active():
        return json.dumps({
            "success": False,
            "error": "LSP service is disabled or inactive for this workspace.",
            "suggestion": "Ensure LSP is enabled in config.yaml and the project is a valid git repository with supported language servers.",
        }, ensure_ascii=False)

    # Convert 1-based line to 0-based LSP protocol line
    lsp_line = max(0, line - 1)
    lsp_char = max(0, character)

    # Coroutine running on the LSP background loop
    async def _run_query():
        client = await service._get_or_spawn(resolved_path)
        if client is None or not client.is_running:
            return {"success": False, "error": f"No active language server found or failed to initialize for '{path}'."}

        # Ensure document is open in LSP server
        try:
            with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            await client.open_file(resolved_path, content)
        except Exception as e:
            logger.debug("LSP open_file notice failed: %s", e)

        uri = file_uri(resolved_path)
        pos = {"line": lsp_line, "character": lsp_char}
        text_doc_pos = {"textDocument": {"uri": uri}, "position": pos}

        action_clean = action.lower().strip()
        method_map = {
            "definition": ("textDocument/definition", text_doc_pos),
            "references": ("textDocument/references", {**text_doc_pos, "context": {"includeDeclaration": True}}),
            "hover": ("textDocument/hover", text_doc_pos),
            "implementation": ("textDocument/implementation", text_doc_pos),
        }

        if action_clean not in method_map:
            return {
                "success": False,
                "error": f"Unsupported action '{action}'. Choose from: definition, references, hover, implementation."
            }

        method, params = method_map[action_clean]
        try:
            res = await client._send_request_with_retry(method, params, timeout=10.0)
        except Exception as exc:
            return {"success": False, "error": f"LSP request '{method}' failed: {exc}"}

        return _format_lsp_response(action_clean, res)

    try:
        result = service._loop.run(_run_query(), timeout=12.0)
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"LSP execution error: {exc}"}, ensure_ascii=False)


def _format_lsp_response(action: str, raw_result: Any) -> Dict[str, Any]:
    """Format raw LSP json-rpc response into clean, agent-readable payload."""
    if raw_result is None:
        return {"success": True, "action": action, "results": [], "message": "No results found"}

    if action == "hover":
        contents = raw_result.get("contents") if isinstance(raw_result, dict) else None
        if not contents:
            return {"success": True, "action": "hover", "info": None}
        hover_text = ""
        if isinstance(contents, str):
            hover_text = contents
        elif isinstance(contents, dict):
            hover_text = contents.get("value", "")
        elif isinstance(contents, list):
            parts = []
            for item in contents:
                parts.append(item.get("value", "") if isinstance(item, dict) else str(item))
            hover_text = "\n".join(parts)
        return {"success": True, "action": "hover", "info": hover_text.strip()}

    # Definition / References / Implementation
    items = raw_result if isinstance(raw_result, list) else [raw_result]
    formatted = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri")
        target_range = item.get("range") or item.get("targetRange") or item.get("targetSelectionRange") or {}
        start_pos = target_range.get("start", {})
        if uri:
            file_path = uri_to_path(uri)
            formatted.append({
                "path": file_path,
                "line": start_pos.get("line", 0) + 1,
                "character": start_pos.get("character", 0),
            })

    return {
        "success": True,
        "action": action,
        "count": len(formatted),
        "results": formatted,
    }


LSP_QUERY_SCHEMA = {
    "name": "lsp_query",
    "description": (
        "Semantic code intelligence via Language Server Protocol (LSP). "
        "Perform exact definition lookups, find all references, inspect type hover/docs, "
        "or find concrete interface implementations with zero hallucination. "
        "Use this instead of grep/search_files when tracing types or dependencies in source code."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["definition", "references", "hover", "implementation"],
                "description": "The LSP query operation to perform.",
            },
            "path": {
                "type": "string",
                "description": "Path to the target source file.",
            },
            "line": {
                "type": "integer",
                "description": "1-based line number where the symbol resides.",
            },
            "character": {
                "type": "integer",
                "description": "0-based character column offset of the symbol. Defaults to 0.",
            },
        },
        "required": ["action", "path", "line"],
    },
}

try:
    from tools.registry import registry

    def _check_lsp_requirements() -> bool:
        service = get_service()
        return service is not None and service.is_active()

    registry.register(
        name="lsp_query",
        toolset="code_intelligence",
        schema=LSP_QUERY_SCHEMA,
        handler=lambda args, **kw: lsp_query_tool(
            action=args.get("action", "definition"),
            path=args.get("path", ""),
            line=int(args.get("line", 1)),
            character=int(args.get("character", 0)),
            task_id=kw.get("task_id", "default"),
        ),
        check_fn=_check_lsp_requirements,
    )
except Exception as _reg_err:
    logger.debug("LSP tool registration deferred: %s", _reg_err)
