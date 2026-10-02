"""Canonical-auth, profile-owned operational framework inspector.

Mutations delegate only saved plan IDs to the validated reversible artifact API.
No client-supplied target, action parameters, or approval objects are accepted.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from contextlib import contextmanager
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from hermes_cli.web_deps import late
from hermes_constants import get_hermes_home

def _authenticate(request: Request):
    late("_require_token")(request)
    # Keep canonical token/cookie authority; forbid cross-origin browser mutation.
    # CLI token callers carry no Origin. Browser fetch sends the current Origin.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if getattr(request.app.state, "auth_required", False) and not origin:
            raise HTTPException(403, "Origin required for gated framework mutation")
        from urllib.parse import urlsplit
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.netloc != request.headers.get("host") or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise HTTPException(403, "Cross-origin framework mutation refused")


router = APIRouter(prefix="/api/framework", dependencies=[Depends(_authenticate)])
_scope = late("_config_profile_scope", "hermes_cli.web_server_profiles")


@contextmanager
def _framework_scope(profile: str | None):
    with _scope(profile):
        home = Path(get_hermes_home())
        base = home / "agent"
        # Reject redirected storage even for reads; missing named profiles never initialize.
        if not home.is_dir():
            raise HTTPException(404, "Profile home unavailable")
        for path in (home, base):
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise HTTPException(409, "Framework storage cannot contain symlinks")
        try:
            yield base
        except (OSError, ValueError, TimeoutError, PermissionError, RuntimeError) as exc:
            raise HTTPException(409, "Framework operation refused or failed") from exc


def _read_record(path: Path, *, yaml_record: bool = False) -> dict:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Redirected framework record")
    if not path.exists():
        return {}
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Framework record exceeds inspector limit")
    text = path.read_text(encoding="utf-8")
    try:
        record = yaml.safe_load(text) if yaml_record else json.loads(text)
    except yaml.YAMLError as exc:
        raise ValueError("Invalid framework record") from exc
    if not isinstance(record, dict):
        raise ValueError("Framework record must be a mapping")
    return record


def _state(base: Path) -> dict:
    return _read_record(base / "state" / "current.yaml", yaml_record=True)


def _records(base: Path, directory_name: str, limit: int) -> list[dict]:
    directory = base / directory_name
    if directory.is_symlink():
        raise ValueError("Redirected plan directory")
    # Names are generated IDs, not user-supplied paths. No storage creation on GET.
    paths = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [_read_record(path) for path in paths[:limit]]


@router.get("/status")
def status(profile: str | None = None):
    with _framework_scope(profile) as base:
        state = _state(base)
        return {"initialized": bool(state), "state": state,
                "capabilities": {"dry_run": True, "apply": True, "approvals": True, "artifact_only": True}}


@router.get("/telemetry")
def telemetry(profile: str | None = None):
    from hermes.platform.agent_framework.pipeline import ObserverAgent
    with _framework_scope(profile) as base:
        return {"telemetry": ObserverAgent(wal_paths=[base.parent / "state.db-wal"]).observe(),
                "scope": "Host counters; WAL belongs to selected profile"}


@router.get("/plans")
def plans(profile: str | None = None, limit: int = Query(20, ge=1, le=100)):
    with _framework_scope(profile) as base:
        return {"plans": _records(base, "plans", limit)}


@router.get("/actions")
def actions(profile: str | None = None, limit: int = Query(20, ge=1, le=100)):
    with _framework_scope(profile) as base:
        return {"receipts": _records(base, "receipts", limit),
                "cycles": [{"cycle_id": record.get("cycle_id"),
                              "plan_id": record.get("plan", {}).get("plan_id"),
                              "outcome": record.get("outcome", "unknown"),
                              "execution_results": record.get("execution_results", []),
                              "verification": record.get("verification", {})}
                             for record in _records(base, "plans", limit)]}


@router.get("/hierarchy")
async def hierarchy(profile: str | None = None):
    from hermes.platform.agent_framework.hierarchy import HierarchicalDiagnostician
    from hermes.platform.agent_framework.pipeline import ObserverAgent
    with _framework_scope(profile) as base:
        supervisor = HierarchicalDiagnostician()
        snapshot = ObserverAgent(wal_paths=[base.parent / "state.db-wal"]).observe()
        result = await supervisor.run(snapshot)
        return {"supervisor": "operational-diagnosis", "allow_child_tasks": False,
                "tasks": [asdict(task) for task in supervisor.partition()],
                "result": result.to_dict()}


class DryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: Literal[True] = True
    create_report: bool = False


@router.post("/run")
def run(body: DryRunRequest, profile: str | None = None):
    from hermes.platform.agent_framework.pipeline import HAOSOrchestrator, PlannerAgent
    with _framework_scope(profile) as base:
        planner = PlannerAgent(report_target=base / "artifacts" / "operational-report.json") if body.create_report else None
        return HAOSOrchestrator(base_dir=base, planner=planner).run_cycle(dry_run=True, autonomous=False)


class StepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    step_id: str = Field(min_length=1, max_length=128)
    confirm: Literal[True]


class GrantRequest(StepRequest):
    autonomous: bool = False


class ApprovalRequest(StepRequest):
    ttl_seconds: int = Field(default=300, ge=1, le=3600)


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    approval_ids: list[str] = Field(default_factory=list, max_length=128)
    mode: Literal["assisted", "autonomous"] = "assisted"
    confirm: Literal[True]


@router.post("/grant")
def grant(body: GrantRequest, profile: str | None = None):
    from hermes.platform.agent_framework.pipeline import HAOSOrchestrator
    with _framework_scope(profile) as base:
        return HAOSOrchestrator(base_dir=base).grant(body.plan_id, body.step_id, autonomous=body.autonomous)


@router.post("/approve")
def approve(body: ApprovalRequest, profile: str | None = None):
    from hermes.platform.agent_framework.pipeline import HAOSOrchestrator
    with _framework_scope(profile) as base:
        return HAOSOrchestrator(base_dir=base).approve(body.plan_id, body.step_id, ttl_seconds=body.ttl_seconds).to_dict()


@router.post("/apply")
def apply(body: ApplyRequest, profile: str | None = None):
    from hermes.platform.agent_framework.pipeline import HAOSOrchestrator
    from fastapi.responses import JSONResponse
    with _framework_scope(profile) as base:
        orchestrator = HAOSOrchestrator(base_dir=base)
        record = orchestrator.run_cycle(mode=body.mode, plan=orchestrator.load_plan(body.plan_id), approvals=body.approval_ids)
        # A refused step is an explicit failed operation, not a successful apply HTTP receipt.
        succeeded = bool(record.get("execution_results")) and all(item.get("success") for item in record["execution_results"])
        return JSONResponse(record, status_code=200 if succeeded else 409)
