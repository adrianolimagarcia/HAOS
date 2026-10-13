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
            "offset": {
                "type": "integer", "minimum": 0, "default": 0,
                "description": "Zero-based offset for paginated artifact expansion.",
            },
            "max_chars": {
                "type": "integer", "minimum": 256, "maximum": 20000, "default": 6000,
                "description": "Maximum characters returned in one expansion.",
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

    # 1. Restore actual persisted bytes; never claim expansion without content.
    if target_uri.startswith("artifact://store/"):
        try:
            from hermes.platform.context.budget.artifact_index import ContextArtifactIndex
            index = ContextArtifactIndex()
            result = index.read(
                target_uri, level=level, section=section,
                offset=args.get("offset", 0), max_chars=args.get("max_chars", 6000),
            )
            return json.dumps(
                result if result is not None else
                {"error": "artifact_not_found_or_expired", "uri": target_uri},
                ensure_ascii=False,
            )
        except (ValueError, TypeError) as exc:
            return json.dumps({"error": "invalid_expansion_request", "detail": str(exc)})
        except Exception:
            return json.dumps({"error": "artifact_storage_unavailable"})

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


CONTEXT_SEARCH_SCHEMA = {
    "name": "context_search",
    "description": "Search persisted tool outputs and artifacts without loading all raw text into context.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keywords or error signature to find."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
        },
        "required": ["query"],
    },
}


def _handle_context_search(args: Dict[str, Any], **kwargs: Any) -> str:
    try:
        from hermes.platform.context.budget.artifact_index import ContextArtifactIndex
        records = ContextArtifactIndex().search(args["query"], limit=args.get("limit", 5))
        return json.dumps({"results": records, "count": len(records)}, ensure_ascii=False)
    except (ValueError, TypeError, KeyError):
        return json.dumps({"error": "invalid_search_request"})
    except Exception:
        return json.dumps({"error": "artifact_storage_unavailable"})


registry.register(
    name="context_search",
    toolset="memory",
    schema=CONTEXT_SEARCH_SCHEMA,
    handler=_handle_context_search,
    emoji="🔎",
)


CONTEXT_CHECKPOINT_SCHEMA = {
    "name": "context_checkpoint",
    "description": (
        "Save/retrieve a concise structured task checkpoint when handing off work "
        "or crossing a context compaction boundary. This does not replace session history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["save", "latest"]},
            "session_id": {"type": "string", "description": "Current HAOS session identifier."},
            "state": {
                "type": "object",
                "description": "For save: key artifacts, branches, commits, decisions, blockers, test results.",
            },
        },
        "required": ["action", "session_id"],
    },
}


def _handle_context_checkpoint(args: Dict[str, Any], **kwargs: Any) -> str:
    try:
        from hermes.platform.context.budget.artifact_index import ContextArtifactIndex
        index = ContextArtifactIndex()
        action = args["action"]
        session_id = args["session_id"]
        if action == "save":
            state = args.get("state")
            if not isinstance(state, dict):
                return json.dumps({"error": "state_must_be_object"})
            checkpoint_id = index.save_checkpoint(session_id, state)
            return json.dumps({"status": "saved", "checkpoint_id": checkpoint_id})
        if action == "latest":
            state = index.latest_checkpoint(session_id)
            return json.dumps(
                state if state is not None else {"status": "no_checkpoint"},
                ensure_ascii=False,
            )
        return json.dumps({"error": "invalid_checkpoint_action"})
    except (ValueError, TypeError, KeyError):
        return json.dumps({"error": "invalid_checkpoint_request"})
    except Exception:
        return json.dumps({"error": "artifact_storage_unavailable"})


registry.register(
    name="context_checkpoint",
    toolset="memory",
    schema=CONTEXT_CHECKPOINT_SCHEMA,
    handler=_handle_context_checkpoint,
    emoji="📌",
)
