"""HAOS Dynamic Form & Operator Intervention Protocol (Budibase-inspired).

Provides declarative, schema-driven form schemas for operator interventions:
- Structured questionnaires (text, number, select, boolean, file).
- Validates submitted answers against the schema.
- Emits structured approvals / input requests for Kanban tasks, CLI (curses),
  and the HAOS Web Dashboard.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("hermes.platform.forms.dynamic")


@dataclass
class FormFieldSpec:
    name: str
    label: str
    type: str  # "text" | "number" | "select" | "boolean" | "textarea"
    required: bool = True
    default: Optional[Any] = None
    options: Optional[List[str]] = None  # for select
    description: Optional[str] = None


@dataclass
class DynamicFormSpec:
    form_id: str
    title: str
    description: str
    fields: List[FormFieldSpec]
    task_id: Optional[str] = None
    action_target: str = "signal_approval"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "form_id": self.form_id,
            "title": self.title,
            "description": self.description,
            "task_id": self.task_id,
            "action_target": self.action_target,
            "fields": [asdict(f) for f in self.fields],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DynamicFormSpec":
        fields = [
            FormFieldSpec(
                name=f["name"],
                label=f.get("label", f["name"]),
                type=f.get("type", "text"),
                required=f.get("required", True),
                default=f.get("default"),
                options=f.get("options"),
                description=f.get("description"),
            )
            for f in data.get("fields", [])
        ]
        return cls(
            form_id=data["form_id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            task_id=data.get("task_id"),
            action_target=data.get("action_target", "signal_approval"),
            fields=fields,
        )

    def validate_submission(self, submission: Dict[str, Any]) -> tuple[bool, Dict[str, str]]:
        """Validate submitted values against field specs. Returns (is_valid, error_dict)."""
        errors = {}
        for f in self.fields:
            val = submission.get(f.name)
            if f.required and (val is None or val == ""):
                errors[f.name] = f"Field '{f.label}' is required."
                continue

            if val is not None and val != "":
                if f.type == "number":
                    try:
                        float(val)
                    except (ValueError, TypeError):
                        errors[f.name] = f"Field '{f.label}' must be a valid number."
                elif f.type == "boolean" and not isinstance(val, bool):
                    if str(val).lower() not in {"true", "false", "1", "0"}:
                        errors[f.name] = f"Field '{f.label}' must be a boolean."
                elif f.type == "select" and f.options and val not in f.options:
                    errors[f.name] = f"Field '{f.label}' must be one of {f.options}."

        return len(errors) == 0, errors

    def to_kanban_marker(self) -> str:
        """Embed this form into task body as a machine-readable block."""
        payload = json.dumps(self.to_dict(), ensure_ascii=False)
        return f"\n\n[DYNAMIC_FORM: {payload}]\n"


def extract_dynamic_form(text: str) -> Optional[DynamicFormSpec]:
    """Parse a [DYNAMIC_FORM: {...}] block from text if present."""
    marker = "[DYNAMIC_FORM:"
    start = text.find(marker)
    if start == -1:
        return None
    content_start = start + len(marker)
    # Track bracket depth to handle nested lists/dicts inside JSON
    depth = 0
    in_str = False
    escape = False
    end = -1
    for i in range(content_start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            if ch == "]" and depth == 0:
                end = i
                break
            depth -= 1

    if end == -1:
        return None
    raw_json = text[content_start:end].strip()
    try:
        data = json.loads(raw_json)
        return DynamicFormSpec.from_dict(data)
    except Exception as e:
        logger.debug("Failed to parse DYNAMIC_FORM: %s", e)
        return None
