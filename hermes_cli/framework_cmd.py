"""Thin operator CLI over the existing framework infrastructure."""

from __future__ import annotations

import json
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


_HANDLERS = {"status": lambda base, args: _status(base),
             "observe": lambda base, args: _observe(base),
             "run": _run, "plan": _plan, "grant": _grant, "approve": _approve, "apply": _apply}


def cmd_framework(args) -> int:
    """Render structured results; expose storage failures as a nonzero exit."""
    try:
        result = _HANDLERS[args.framework_action](_base_dir(args), args)
    except (OSError, ValueError, TimeoutError, RuntimeError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "action": args.framework_action}))
        else:
            print(f"Framework {args.framework_action} failed: {exc}")
        return 1
    if not args.json:
        print(f"HAOS framework: {args.framework_action} (bounded operator surface)")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
