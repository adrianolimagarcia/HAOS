"""Experimental, opt-in registry for declarative tool capabilities.

Execution is fail-closed: unknown, malformed, unauthorized, or timed-out calls never
fall through to a less restrictive handler. A supplied fallback is used only when
an authorized operation has no registered v2 executor.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional


@dataclass
class CapabilityOperation:
    id: str
    tool_ref: str
    name: str
    description: str
    parameters_compact: dict
    parameters_schema: dict
    read_only: bool = True
    roles: list[str] = field(default_factory=lambda: ["default"])
    risk_level: str = "low"
    timeout_seconds: int = 60
    side_effects: list[str] = field(default_factory=list)
    cost_tier: str = "free"


class CapabilityRegistryV2:
    """In-memory operation registry with deterministic retrieval and guarded dispatch."""

    def __init__(self) -> None:
        self._operations: dict[str, CapabilityOperation] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._lock = threading.RLock()
        self.audit_traces: list[dict[str, Any]] = []

    def register(self, operation: CapabilityOperation, handler: Callable[..., Any] | None = None) -> None:
        if not isinstance(operation, CapabilityOperation) or not operation.id:
            raise ValueError("operation must be a CapabilityOperation with a non-empty id")
        with self._lock:
            self._operations[operation.id] = operation
            if handler is not None:
                self._handlers[operation.id] = handler

    def export_compact_manifest(self, role: str = "default") -> list[dict]:
        with self._lock:
            operations = list(self._operations.values())
        return [
            {"id": op.id, "tool_ref": op.tool_ref, "name": op.name,
             "description": _summary(op.description), "parameters": _compact_parameters(op),
             "read_only": op.read_only}
            for op in operations if role in op.roles
        ]

    def get_operation(self, op_id: str) -> Optional[CapabilityOperation]:
        with self._lock:
            return self._operations.get(op_id)

    def search_capabilities(self, query: str, role: str = "default", limit: int = 5) -> list[CapabilityOperation]:
        if limit <= 0 or not isinstance(query, str) or not query.strip():
            return []
        terms = set(_tokens(query))
        if not terms:
            return []
        with self._lock:
            operations = list(self._operations.values())
        scored = []
        for op in operations:
            if role not in op.roles:
                continue
            haystack = set(_tokens(f"{op.id} {op.tool_ref} {op.name} {op.description} {' '.join(op.parameters_schema.get('properties', {}))}"))
            overlap = terms & haystack
            if overlap:
                scored.append((len(overlap) / len(terms), len(overlap) / len(haystack), op.id, op))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return [item[3] for item in scored[:limit]]

    def execute(self, op_id: str, args: dict, caller_role: str = "default", caller_profile: str = "default", fallback_handler=None) -> dict:
        started = time.monotonic()
        op = self.get_operation(op_id)
        risk = op.risk_level if op else "unknown"
        try:
            if op is None:
                return self._failure(404, "operation_not_found", "Unknown operation", op_id, started, risk)
            if caller_role not in op.roles:
                return self._failure(403, "forbidden", "Caller role is not permitted", op_id, started, risk)
            if not isinstance(args, dict):
                return self._failure(400, "invalid_arguments", "Arguments must be an object", op_id, started, risk)
            error = _validate_args(op.parameters_schema, args)
            if error:
                return self._failure(400, "invalid_arguments", error, op_id, started, risk)
            if not op.read_only and caller_role in {"default", "restricted", "read_only"}:
                return self._failure(403, "read_only_enforced", "Mutating operation is not permitted for a restricted caller", op_id, started, risk)
            with self._lock:
                handler = self._handlers.get(op_id)
            handler = handler or fallback_handler
            if handler is None:
                return self._failure(501, "handler_unavailable", "No handler is registered", op_id, started, risk)
            result = _run_with_timeout(handler, args, max(1, op.timeout_seconds))
            return self._record({"ok": True, "result": result}, op_id, started, "allow", risk)
        except TimeoutError:
            return self._failure(504, "timeout", "Operation timed out", op_id, started, risk)
        except Exception as exc:
            return self._failure(500, "execution_error", str(exc)[:300], op_id, started, risk)

    def _failure(self, status: int, code: str, message: str, op_id: str, started: float, risk: str) -> dict:
        return self._record({"ok": False, "error": {"status": status, "code": code, "message": message}}, op_id, started, "deny" if status < 500 else "error", risk)

    def _record(self, response: dict, op_id: str, started: float, verdict: str, risk: str) -> dict:
        trace = {"op_id": op_id, "timestamp": datetime.now(timezone.utc).isoformat(), "duration_ms": max(0, round((time.monotonic() - started) * 1000)), "verdict": verdict, "risk": risk}
        with self._lock:
            self.audit_traces.append(trace)
        response["trace"] = trace
        return response


def is_v2_enabled() -> bool:
    """Read env override first, then config; absent or malformed configuration is off."""
    raw = os.getenv("HAOS_MCP_CAPABILITY_REGISTRY_V2")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    try:
        from hermes_cli.config import load_config_readonly
        config = load_config_readonly() or {}
        return (config.get("capabilities", {}).get("registry_v2", {}).get("enabled") is True)
    except Exception:
        return False


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _summary(text: str, max_length: int = 100) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= max_length else text[:max_length - 1].rstrip() + "…"


def _compact_parameters(op: CapabilityOperation) -> dict:
    schema = op.parameters_schema or {}
    properties = schema.get("properties", {})
    compact = {}
    for name, detail in properties.items():
        if not isinstance(detail, dict):
            continue
        item = {"type": detail.get("type", "object")}
        if detail.get("description"):
            item["summary"] = _summary(detail["description"], 40)
        compact[name] = item
    return {"required": list(schema.get("required", [])), "properties": compact}


def _validate_args(schema: dict, args: dict) -> str | None:
    if not isinstance(schema, dict):
        return "Operation schema is invalid"
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        return "Operation schema is invalid"
    missing = [key for key in required if key not in args]
    if missing:
        return "Missing required fields: " + ", ".join(map(str, missing))
    for key, value in args.items():
        if key not in properties:
            return f"Unexpected argument: {key}"
        detail = properties[key]
        if not isinstance(detail, dict):
            return f"Invalid schema for argument: {key}"
        expected = detail.get("type")
        if not _matches_type(value, expected):
            return f"Argument {key} must be {expected}"
    return None


def _matches_type(value: Any, expected: Any) -> bool:
    types = {"string": lambda v: isinstance(v, str), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool), "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), "boolean": lambda v: isinstance(v, bool), "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list), "null": lambda v: v is None}
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    return types.get(expected, lambda _v: False)(value)


def _run_with_timeout(handler: Callable[..., Any], args: dict, timeout: int) -> Any:
    """Run handlers in a daemon worker; timeout returns promptly without blocking shutdown."""
    result: list[Any] = []
    failure: list[BaseException] = []
    done = threading.Event()
    def invoke() -> None:
        try:
            result.append(handler(args))
        except BaseException as exc:
            failure.append(exc)
        finally:
            done.set()
    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    if not done.wait(timeout):
        raise TimeoutError
    if failure:
        raise failure[0]
    return result[0] if result else None
