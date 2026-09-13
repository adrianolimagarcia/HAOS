"""HAOS Instincts Tool: Model-facing tool to record or query instincts in real time."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from hermes.platform.memory.instincts import InstinctStore
from tools.registry import registry

logger = logging.getLogger("tools.instincts")


def instincts_tool(
    action: str,
    rule: Optional[str] = None,
    category: str = "workflow",
    project_scope: str = "default",
    feedback: Optional[str] = None,  # "reinforce" | "penalize"
) -> str:
    """Atomic Instinct management tool for continuous learning.

    Args:
        action: 'record' | 'list' | 'reinforce' | 'penalize'
        rule: Atomic rule/heuristic (for record) or instinct_id (for feedback)
        category: 'workflow' | 'tool' | 'test' | 'syntax' | 'domain'
        project_scope: Project identifier / workspace slug
        feedback: Feedback type
    """
    store = InstinctStore()
    act = action.lower().strip()

    if act == "record":
        if not rule or not rule.strip():
            return json.dumps({"success": False, "error": "Rule text cannot be empty."}, ensure_ascii=False)
        instinct = store.record_instinct(rule, category=category, project_scope=project_scope)
        return json.dumps({
            "success": True,
            "action": "record",
            "instinct": instinct.to_dict(),
            "message": f"Instinct recorded with confidence {instinct.confidence:.2f}."
        }, ensure_ascii=False)

    elif act == "list":
        instincts = store.load_instincts(project_scope)
        serialized = [ins.to_dict() for ins in instincts.values()]
        return json.dumps({
            "success": True,
            "action": "list",
            "project_scope": project_scope,
            "count": len(serialized),
            "instincts": serialized,
        }, ensure_ascii=False)

    elif act in ("reinforce", "penalize"):
        instincts = store.load_instincts(project_scope)
        target_id = rule.strip() if rule else ""
        if target_id not in instincts:
            return json.dumps({"success": False, "error": f"Instinct '{target_id}' not found in {project_scope}."}, ensure_ascii=False)
        ins = instincts[target_id]
        if act == "reinforce":
            ins.reinforce()
        else:
            ins.penalize()
        store.save_instincts(project_scope, instincts)
        return json.dumps({
            "success": True,
            "action": act,
            "instinct_id": target_id,
            "new_confidence": ins.confidence,
        }, ensure_ascii=False)

    return json.dumps({"success": False, "error": f"Unknown action: {action}"}, ensure_ascii=False)


INSTINCTS_SCHEMA = {
    "name": "instinct_manage",
    "description": (
        "Manage atomic project-scoped instincts with confidence scoring (continuous learning). "
        "Use 'record' when discovering a specific repository rule, pitfall, or tip. "
        "Instincts with high confidence (>= 0.8) are automatically promoted to permanent skills by the Ouroboros engine."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["record", "list", "reinforce", "penalize"],
                "description": "Operation to perform.",
            },
            "rule": {
                "type": "string",
                "description": "The atomic rule statement (for record) or instinct_id (for reinforce/penalize).",
            },
            "category": {
                "type": "string",
                "enum": ["workflow", "tool", "test", "syntax", "domain"],
                "description": "Category classification.",
            },
            "project_scope": {
                "type": "string",
                "description": "Project identifier (defaults to current project).",
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="instinct_manage",
    toolset="memory",
    schema=INSTINCTS_SCHEMA,
    handler=lambda args, **kw: instincts_tool(
        action=args.get("action", "list"),
        rule=args.get("rule"),
        category=args.get("category", "workflow"),
        project_scope=args.get("project_scope", "default"),
    ),
)
