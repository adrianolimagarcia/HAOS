"""Thin operator CLI over the existing framework infrastructure."""

from __future__ import annotations

import hashlib
import json
import signal
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path


def _base_dir(args) -> Path:
    if args.base_dir is not None:
        return Path(args.base_dir).resolve()
    from hermes_constants import get_hermes_home

    # Resolve at invocation time: one interpreter may inspect multiple profiles.
    return (Path(get_hermes_home()) / "agent").resolve()


def _status(base_dir: Path) -> dict:
    if not (base_dir / "state" / "current.yaml").is_file():
        return {"base_dir": str(base_dir), "initialized": False, "state": {}}
    from hermes.platform.agent_framework.state_manager import HAOSStateManager

    return {"base_dir": str(base_dir), "initialized": True,
            "state": HAOSStateManager(base_dir=base_dir).get_state()}


def _observe(base_dir: Path) -> dict:
    from hermes.platform.agent_framework.pipeline import ObserverAgent

    return {"base_dir": str(base_dir), "telemetry": ObserverAgent(
        wal_paths=[base_dir.parent / "state.db-wal"]
    ).observe()}


def _run(base_dir: Path, args=None) -> dict:
    from hermes.platform.agent_framework.pipeline import HAOSOrchestrator

    return HAOSOrchestrator(base_dir=base_dir).run_cycle(dry_run=True, autonomous=False)


def _plan(base_dir: Path, args) -> dict:
    from hermes.platform.agent_framework.pipeline import HAOSOrchestrator, PlannerAgent

    target = base_dir / "artifacts" / "operational-report.json" if args.report else None
    return HAOSOrchestrator(base_dir=base_dir, planner=PlannerAgent(report_target=target)).run_cycle()


def _grant(base_dir: Path, args) -> dict:
    from hermes.platform.agent_framework.pipeline import HAOSOrchestrator

    return HAOSOrchestrator(base_dir=base_dir).grant(
        args.plan_id, args.step_id, autonomous=args.autonomous
    )


def _approve(base_dir: Path, args) -> dict:
    from hermes.platform.agent_framework.pipeline import HAOSOrchestrator

    return HAOSOrchestrator(base_dir=base_dir).approve(
        args.plan_id, args.step_id, ttl_seconds=args.ttl_seconds
    ).to_dict()


def _apply(base_dir: Path, args) -> dict:
    from hermes.platform.agent_framework.pipeline import HAOSOrchestrator

    orchestrator = HAOSOrchestrator(base_dir=base_dir)
    # Only persisted plans can cross the operator apply boundary; no raw plan path.
    plan = orchestrator.load_plan(args.plan_id)
    return orchestrator.run_cycle(mode=args.mode, plan=plan, approvals=args.approvals)


def _autonomy_status(base_dir: Path, args) -> dict:
    from hermes.platform.agent_framework.autonomy_config import AutonomyConfig
    from hermes.platform.agent_framework.event_queue import EventQueue
    from hermes.platform.agent_framework.state_manager import HAOSStateManager

    heartbeat_path = base_dir / "autonomy" / "service.json"
    HAOSStateManager._check_path(heartbeat_path)
    heartbeat = json.loads(heartbeat_path.read_text()) if heartbeat_path.is_file() else None
    if heartbeat is not None and (not isinstance(heartbeat, dict) or
            type(heartbeat.get("updated_at")) not in (int, float)):
        raise ValueError("Invalid autonomy service heartbeat")
    stale = heartbeat is None or time.time() - heartbeat["updated_at"] > 120
    return {"base_dir": str(base_dir), "config": asdict(AutonomyConfig.load()),
            "queue": EventQueue.inspect(base_dir), "heartbeat": heartbeat,
            "heartbeat_stale": stale}


def _autonomy_pause(base_dir: Path, args) -> dict:
    from hermes.platform.agent_framework.event_queue import EventQueue

    queue = EventQueue(base_dir)
    try:
        return {"paused": queue.set_pause(args.autonomy_action == "pause")}
    finally:
        queue.close()


def _autonomy_event(base_dir: Path, args) -> dict:
    from hermes.platform.agent_framework.autonomy_config import AutonomyConfig
    from hermes.platform.agent_framework.event_queue import EventQueue
    from hermes.platform.agent_framework.models import HAOSEvent

    summary = args.summary.strip()
    if not summary or len(args.summary) > 2000:
        raise ValueError("summary must contain 1..2000 characters of text")
    config = AutonomyConfig.load()
    queue = EventQueue(base_dir, max_pending=config.max_pending,
                       cooldown_seconds=config.cooldown_seconds)
    try:
        # A description is untrusted data, never executable input or an authorization.
        result = queue.submit(HAOSEvent(event_type="operator.investigation", source="cli",
                              payload={"summary": summary,
                                       "issue_id": hashlib.sha256(summary.encode("utf-8")).hexdigest()}))
        if not result["accepted"]:
            raise ValueError(f"Event not accepted: {result['reason']}")
        return result
    finally:
        queue.close()


def _autonomy_serve(base_dir: Path, args) -> dict:
    from hermes.platform.agent_framework.autonomy_config import AutonomyConfig
    from hermes.platform.agent_framework.autonomy_service import AutonomyService

    config = AutonomyConfig.load()
    if not config.enabled:
        raise ValueError("Enable framework.autonomy.enabled in config.yaml before serving")
    stop_event = threading.Event()
    previous = {}
    service = AutonomyService(base_dir=base_dir)
    try:
        # Supervisors stop this foreground worker; no detached process or installation.
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda _signum, _frame: stop_event.set())
        service.run(stop_event)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        service.close()
    return {"status": "stopped"}


_AUTONOMY_HANDLERS = {"status": _autonomy_status, "pause": _autonomy_pause,
                      "resume": _autonomy_pause, "event": _autonomy_event,
                      "serve": _autonomy_serve}


def _autonomy(base_dir: Path, args) -> dict:
    return _AUTONOMY_HANDLERS[args.autonomy_action](base_dir, args)


_HANDLERS = {"status": lambda base, args: _status(base),
             "observe": lambda base, args: _observe(base),
             "run": _run, "plan": _plan, "grant": _grant, "approve": _approve,
             "apply": _apply, "autonomy": _autonomy}


def cmd_framework(args) -> int:
    """Render structured results; expose storage failures as a nonzero exit."""
    try:
        result = _HANDLERS[args.framework_action](_base_dir(args), args)
    except (OSError, ValueError, TimeoutError, RuntimeError, sqlite3.DatabaseError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "action": args.framework_action}))
        else:
            print(f"Framework {args.framework_action} failed: {exc}")
        return 1
    if not args.json:
        print(f"HAOS framework: {args.framework_action} (bounded operator surface)")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
