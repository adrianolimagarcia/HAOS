"""Experimental, opt-in registry for declarative tool capabilities.

Execution is fail-closed: unknown, malformed, unauthorized, or timed-out calls never
fall through to a less restrictive handler. A supplied fallback is used only when
an authorized operation has no registered v2 executor.
"""
from __future__ import annotations

import json
import multiprocessing
import queue
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class SecurityContext:
    principal_id: str
    role: str = "default"
    profile: str = "default"
    authenticated: bool = False
    session_id: Optional[str] = None
    claims: dict = field(default_factory=dict)


def create_trusted_security_context(
    principal_id: str,
    role: str,
    profile: str = "default",
    session_id: Optional[str] = None,
    claims: Optional[dict] = None,
) -> SecurityContext:
    """Create a verified/authenticated security context."""
    if not principal_id or not isinstance(principal_id, str):
        raise ValueError("principal_id must be a non-empty string")
    return SecurityContext(
        principal_id=principal_id,
        role=role or "default",
        profile=profile or "default",
        authenticated=True,
        session_id=session_id,
        claims=claims or {},
    )


def create_anonymous_security_context(profile: str = "default") -> SecurityContext:
    """Create an unauthenticated default security context."""
    return SecurityContext(
        principal_id="anonymous",
        role="default",
        profile=profile or "default",
        authenticated=False,
    )


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
        if not isinstance(operation.tool_ref, str) or not operation.tool_ref.strip():
            raise ValueError("operation must have a valid non-empty tool_ref")
        if not isinstance(operation.name, str) or not operation.name.strip():
            raise ValueError("operation must have a valid non-empty name")
        if not isinstance(operation.parameters_schema, dict):
            raise ValueError("operation parameters_schema must be a dictionary")
        if not isinstance(operation.roles, list):
            raise ValueError("operation roles must be a list of strings")
        if operation.risk_level not in {"low", "medium", "high", "critical", "unknown"}:
            raise ValueError(f"operation risk_level '{operation.risk_level}' is invalid")
        if operation.cost_tier not in {"free", "low", "medium", "high", "enterprise"}:
            raise ValueError(f"operation cost_tier '{operation.cost_tier}' is invalid")
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

    def get_audit_traces(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(t) for t in self.audit_traces]

    def clear_audit_traces(self) -> None:
        with self._lock:
            self.audit_traces.clear()

    def execute(
        self,
        op_id: str,
        args: dict,
        caller_role: Optional[str] = None,
        caller_profile: Optional[str] = None,
        fallback_handler=None,
        security_context: Optional[SecurityContext] = None,
        check_feature_flag: bool = False,
    ) -> dict:
        started = time.monotonic()
        op = self.get_operation(op_id)
        risk = op.risk_level if op else "unknown"

        if check_feature_flag and not is_v2_enabled():
            return self._failure(
                503,
                "feature_disabled",
                "Capability Registry v2 is disabled by feature flag",
                op_id,
                started,
                risk,
                caller_role,
                caller_profile,
            )
        # Resolve SecurityContext fail-closed against role spoofing
        if security_context is not None:
            if not isinstance(security_context, SecurityContext):
                return self._failure(403, "invalid_security_context", "Security context is invalid", op_id, started, risk, "untrusted", caller_profile or "default")
            effective_role = security_context.role
            effective_profile = security_context.profile
            effective_authenticated = security_context.authenticated
        else:
            # Caller passed arbitrary caller_role without SecurityContext:
            if caller_role is not None and caller_role not in {"default", "restricted", "read_only"}:
                return self._failure(
                    403,
                    "role_spoofing_prevented",
                    f"Unauthenticated caller cannot declare privileged role '{caller_role}' without a trusted SecurityContext",
                    op_id,
                    started,
                    risk,
                    "unauthenticated",
                    caller_profile or "default",
                )
            effective_role = caller_role or "default"
            effective_profile = caller_profile or "default"
            effective_authenticated = False

        if effective_role not in {"default", "restricted", "read_only"} and not effective_authenticated:
            return self._failure(
                403,
                "authentication_required",
                f"Role '{effective_role}' requires an authenticated SecurityContext",
                op_id,
                started,
                risk,
                effective_role,
                effective_profile,
            )

        try:
            if op is None:
                return self._failure(404, "operation_not_found", "Unknown operation", op_id, started, risk, effective_role, effective_profile)
            if not isinstance(effective_role, str) or not effective_role.strip():
                return self._failure(403, "forbidden", "Caller role is invalid", op_id, started, risk, effective_role, effective_profile)
            if effective_role not in op.roles:
                return self._failure(403, "forbidden", "Caller role is not permitted", op_id, started, risk, effective_role, effective_profile)
            if not isinstance(args, dict):
                return self._failure(400, "invalid_arguments", "Arguments must be an object", op_id, started, risk, effective_role, effective_profile)
            error = _validate_args(op.parameters_schema, args)
            if error:
                return self._failure(400, "invalid_arguments", error, op_id, started, risk, effective_role, effective_profile)
            if not op.read_only and effective_role in {"default", "restricted", "read_only"}:
                return self._failure(403, "read_only_enforced", "Mutating operation is not permitted for a restricted caller", op_id, started, risk, effective_role, effective_profile)
            with self._lock:
                handler = self._handlers.get(op_id)
            handler = handler or fallback_handler
            if handler is None:
                return self._failure(501, "handler_unavailable", "No handler is registered", op_id, started, risk, effective_role, effective_profile)
            result = _run_with_timeout(handler, args, max(1, op.timeout_seconds))
            return self._record({"ok": True, "result": result}, op_id, started, "allow", risk, effective_role, effective_profile, 200)
        except TimeoutError:
            return self._failure(504, "timeout", "Operation timed out", op_id, started, risk, effective_role, effective_profile)
        except Exception as exc:
            return self._failure(500, "execution_error", str(exc)[:300], op_id, started, risk, effective_role, effective_profile)

    def _failure(self, status: int, code: str, message: str, op_id: str, started: float, risk: str, caller_role: str = "default", caller_profile: str = "default") -> dict:
        return self._record({"ok": False, "error": {"status": status, "code": code, "message": message}}, op_id, started, "deny" if status < 500 else "error", risk, caller_role, caller_profile, status)

    def _record(self, response: dict, op_id: str, started: float, verdict: str, risk: str, caller_role: str = "default", caller_profile: str = "default", status: int = 200) -> dict:
        trace = {
            "op_id": op_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
            "verdict": verdict,
            "status": status,
            "risk": risk,
            "caller_role": caller_role,
            "caller_profile": caller_profile,
        }
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
        # Validate nested objects and array items if schema defines them
        if expected == "object" and isinstance(value, dict) and "properties" in detail:
            nested_err = _validate_args(detail, value)
            if nested_err:
                return f"Argument {key}.{nested_err}"
        elif expected == "array" and isinstance(value, list) and "items" in detail and isinstance(detail["items"], dict):
            item_expected = detail["items"].get("type")
            for idx, item_val in enumerate(value):
                if not _matches_type(item_val, item_expected):
                    return f"Argument {key}[{idx}] must be {item_expected}"
    return None


def _matches_type(value: Any, expected: Any) -> bool:
    types = {"string": lambda v: isinstance(v, str), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool), "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), "boolean": lambda v: isinstance(v, bool), "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list), "null": lambda v: v is None}
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    return types.get(expected, lambda _v: False)(value)


def _run_with_timeout(handler: Callable[..., Any], args: dict, timeout: int | float) -> Any:
    """Run handlers with an active termination guard to prevent zombie worker side effects."""
    try:
        ctx = multiprocessing.get_context("fork")
        q = ctx.Queue()

        def worker_target() -> None:
            try:
                res = handler(args)
                q.put(("ok", res))
            except BaseException as exc:
                q.put(("err", exc))

        proc = ctx.Process(target=worker_target)
        proc.start()
        proc.join(timeout)
        if proc.is_alive():
            proc.terminate()
            proc.join(0.05)
            if proc.is_alive():
                proc.kill()
                proc.join(0.05)
            proc.close()
            raise TimeoutError("Operation timed out and worker process was terminated")

        try:
            status, payload = q.get(timeout=0.2)
        except queue.Empty:
            status, payload = ("err", RuntimeError("Worker process exited without returning a result"))
        proc.close()
        if status == "err":
            raise payload
        return payload
    except (ValueError, AttributeError):
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
