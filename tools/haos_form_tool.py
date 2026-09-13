"""HAOS Request Operator Form Tool.

Allows an autonomous agent to request structured intervention or approval
from a human operator via a typed form (Budibase-inspired).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from hermes.platform.forms.dynamic import DynamicFormSpec, FormFieldSpec
from tools.registry import registry

logger = logging.getLogger("tools.operator_form")


def request_operator_form_tool(
    form_id: str,
    title: str,
    description: str,
    fields: List[Dict[str, Any]],
    task_id: Optional[str] = None,
) -> str:
    """Emit a declarative form specification for operator input.

    Args:
        form_id: Unique slug for the form.
        title: Human-readable title.
        description: Instructions or context for the human.
        fields: List of field definitions [{'name': ..., 'label': ..., 'type': 'text|number|select|boolean|textarea', ...}].
        task_id: Optional associated task id.
    """
    try:
        field_specs = [
            FormFieldSpec(
                name=f["name"],
                label=f.get("label", f["name"]),
                type=f.get("field_type") or f.get("type", "text"),
                required=bool(f.get("required", True)),
                default=f.get("default"),
                options=f.get("options"),
                description=f.get("description"),
            )
            for f in fields
        ]
        spec = DynamicFormSpec(
            form_id=form_id,
            title=title,
            description=description,
            fields=field_specs,
            task_id=task_id,
        )
        return json.dumps({
            "success": True,
            "form": spec.to_dict(),
            "marker": spec.to_kanban_marker(),
            "message": "Dynamic operator form generated successfully.",
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


REQUEST_FORM_SCHEMA = {
    "name": "request_operator_form",
    "description": (
        "Request structured input or approval from the human operator via a declarative dynamic form. "
        "Use this instead of asking unformatted questions in chat when you need typed parameters "
        "(e.g., API keys, deployment target choices, confirmation booleans, or migration parameters)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "form_id": {
                "type": "string",
                "description": "Unique identifier for this form request.",
            },
            "title": {
                "type": "string",
                "description": "Short, clear title for the operator prompt.",
            },
            "description": {
                "type": "string",
                "description": "Context and instructions explaining why this input is needed.",
            },
            "fields": {
                "type": "array",
                "description": "List of typed form fields.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Field key in submission payload."},
                        "label": {"type": "string", "description": "User-facing label."},
                        "field_type": {
                            "type": "string",
                            "enum": ["text", "number", "select", "boolean", "textarea"],
                            "description": "Input widget type.",
                        },
                        "required": {"type": "boolean", "description": "Whether the field is mandatory."},
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Allowed choices if type is select.",
                        },
                        "description": {"type": "string", "description": "Helper description under the field."},
                    },
                    "required": ["name", "label"],
                },
            },
            "task_id": {
                "type": "string",
                "description": "Optional Kanban task ID this form attaches to.",
            },
        },
        "required": ["form_id", "title", "description", "fields"],
    },
}

registry.register(
    name="request_operator_form",
    toolset="clarify",
    schema=REQUEST_FORM_SCHEMA,
    handler=lambda args, **kw: request_operator_form_tool(
        form_id=args.get("form_id", "form-1"),
        title=args.get("title", ""),
        description=args.get("description", ""),
        fields=args.get("fields", []),
        task_id=args.get("task_id") or kw.get("task_id"),
    ),
)
