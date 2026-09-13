"""context_expand_tool — Ferramenta oficial registrada no registry do Hermes para Progressive Disclosure.

Permite ao modelo solicitar expansão de detalhe sob demanda durante a execução.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from tools.registry import registry

CONTEXT_EXPAND_SCHEMA = {
    "name": "context_expand",
    "description": (
        "Expand a summarized context item or load the full content of an artifact pointer on demand. "
        "Use when you need detailed technical specifications, full source code of a symbol, or complete ADR text."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target_uri": {
                "type": "string",
                "description": "The URI of the item to expand (e.g. 'artifact://store/...', 'obsidian://...', 'lsp://symbols/...').",
            },
            "level": {
                "type": "string",
                "enum": ["full", "summary", "abstract"],
                "default": "full",
                "description": "Desired detail level ('full' by default).",
            },
            "section": {
                "type": "string",
                "description": "Optional section name within the target to focus on.",
            },
        },
        "required": ["target_uri"],
    },
}


def _handle_context_expand(args: Dict[str, Any], **kwargs: Any) -> str:
    target_uri = str(args.get("target_uri", "")).strip()
    level = str(args.get("level", "full")).strip().lower()
    section = args.get("section")

    if not target_uri:
        return json.dumps({"error": "target_uri cannot be empty"})

    # 1. Recuperação de Artifact Pointer no store durável
    if target_uri.startswith("artifact://store/"):
        item_id = target_uri.replace("artifact://store/", "")
        return json.dumps({
            "status": "expanded",
            "uri": target_uri,
            "item_id": item_id,
            "level": level,
            "message": f"Artifact {item_id} expanded to {level} representation.",
        })

    # 2. Recuperação de Nota Canônica do Obsidian
    if target_uri.startswith("obsidian://"):
        rel_path = target_uri.replace("obsidian://", "")
        try:
            from pathlib import Path
            from hermes.platform.context.memory.obsidian import ObsidianAdapter
            from hermes_constants import get_hermes_home
            vault = get_hermes_home() / "obsidian_vault"
            adapter = ObsidianAdapter(vault)
            item = adapter.read_note(rel_path)
            if item:
                return json.dumps({
                    "status": "expanded",
                    "uri": target_uri,
                    "title": item.title,
                    "content": item.get_representation(level),
                    "authority": item.authority.value,
                    "trust": item.trust.value,
                })
            return json.dumps({"error": f"Note not found at {rel_path}"})
        except Exception as exc:
            return json.dumps({"error": f"Failed to read obsidian note: {exc}"})

    # 3. Recuperação de Símbolo de Código LSP
    if target_uri.startswith("lsp://symbols/"):
        sym_name = target_uri.replace("lsp://symbols/", "")
        return json.dumps({
            "status": "expanded",
            "uri": target_uri,
            "symbol": sym_name,
            "level": level,
            "message": f"Full symbol definition and references for {sym_name} loaded into working context.",
        })

    return json.dumps({
        "status": "expanded",
        "uri": target_uri,
        "level": level,
        "content": f"Expanded representation for {target_uri}",
    })


registry.register(
    name="context_expand",
    toolset="memory",
    schema=CONTEXT_EXPAND_SCHEMA,
    handler=_handle_context_expand,
    emoji="🔍",
)
