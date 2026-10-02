"""Bounded operator-edge observation pipeline, not a replacement for agent_mesh.

No shell execution, prompt mutation, or inferred remediation. Handlers are trusted
in-process integrations: registering one does not bypass policy or confirmation.
"""
from __future__ import annotations

import math
import time
import uuid
from pathlib import Path
from typing import Callable, Any

from .event_bus import HAOSEventBus
from .memory_store import OperationalMemoryStore
from .models import (Diagnosis, EvidenceItem, ExecutionPlan, ExecutionResult,
                     HAOSEvent, PlanStatus, PlanStep, StepStatus, VerificationResult)
from .policy import AgentPolicyEngine
from .state_manager import HAOSStateManager
from .actions import WorkspaceConfigUpdate
from .approvals import ApprovalStore
from .receipts import ReceiptStore


def _bound(value: int, ceiling: int, name: str) -> int:
    if type(value) is not int or not 1 <= value <= ceiling:
        raise ValueError(f"{name} must be an integer in [1, {ceiling}]")
    return value


class ObserverAgent:
    """Read real proc/sys files; missing or malformed sources remain unknown.

    Roots are injectable for tests, not simulated platform identity. Cumulative
    disk counters and load averages do not prove storage/CPU saturation.
    """

    def __init__(self, proc_root: Path | str = "/proc", sys_root: Path | str = "/sys",
                 wal_paths: list[Path | str] | None = None):
        self.proc_root = Path(proc_root)
        self.sys_root = Path(sys_root)
        self.wal_paths = [Path(p) for p in (wal_paths or [])]

    def observe(self) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        errors: dict[str, str] = {}
        sources: dict[str, dict[str, Any]] = {}
        readers = {
            "loadavg": (self.proc_root / "loadavg", self._load),
            "meminfo": (self.proc_root / "meminfo", self._memory),
            "diskstats": (self.proc_root / "diskstats", self._disks),
            "vm_dirty_ratio": (self.proc_root / "sys/vm/dirty_ratio", self._integer),
            "cpu_online": (self.sys_root / "devices/system/cpu/online", self._online),
        }
        for name, (path, parse) in readers.items():
            try:
                value = parse(path.read_text(encoding="utf-8"))
                metrics.update(value)
                sources[name] = {"status": "observed", "path": str(path)}
            except (OSError, UnicodeError, ValueError, KeyError, IndexError) as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
                sources[name] = {"status": "unknown", "path": str(path), "error": errors[name]}
        for name, collect in (("bdi", self._bdi), ("drive_temperatures", self._temperatures),
                              ("wal_files", self._wal)):
            try:
                metrics[name] = collect()
                sources[name] = ({"status": "observed"} if metrics[name] else
                                 {"status": "unknown", "reason": "No matching sources or explicit targets available"})
            except (OSError, UnicodeError, ValueError) as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
                sources[name] = {"status": "unknown", "error": errors[name]}
        return {"timestamp": time.time(), "metrics": metrics, "errors": errors,
                "sources": sources, "services": {"status": "unknown", "reason": "Not probed"}}

    def _bdi(self) -> list[dict[str, Any]]:
        result = []
        for block in sorted((self.sys_root / "block").glob("*"))[:128]:
            entry = block / "bdi"
            if not entry.exists():
                continue
            device = block / "device"
            resolved = device.resolve(strict=True) if device.exists() else None
            # USB identity comes from sysfs subsystem ancestry, never device-name guesses.
            usb = False
            if resolved is not None:
                for parent in (resolved, *resolved.parents):
                    subsystem = parent / "subsystem"
                    if subsystem.is_symlink() and subsystem.resolve().name == "usb":
                        usb = True
                        break
            values = {}
            for name in ("min_ratio", "max_ratio", "strict_limit", "max_bytes"):
                path = entry / name
                if path.exists():
                    values[name] = int(path.read_text().strip())
            result.append({"name": block.name, "bdi_path": str(entry.resolve()), "device_path": str(resolved) if resolved else None,
                           "transport": "usb" if usb else "unknown", **values})
        return result

    def _temperatures(self) -> list[dict[str, Any]]:
        result = []
        for entry in sorted((self.sys_root / "class/hwmon").glob("hwmon*"))[:128]:
            name = (entry / "name").read_text().strip()
            if name not in {"nvme", "drivetemp"}:
                continue
            for sensor in sorted(entry.glob("temp*_input"))[:32]:
                value = int(sensor.read_text().strip()) / 1000
                if not -100 <= value <= 250:
                    raise ValueError("Invalid drive temperature")
                result.append({"driver": name, "sensor": str(sensor), "celsius": value})
        return result

    def _wal(self) -> list[dict[str, Any]]:
        result = []
        for path in self.wal_paths[:128]:
            try:
                size = path.stat().st_size
                result.append({"path": str(path), "status": "observed", "size_bytes": size})
            except FileNotFoundError:
                result.append({"path": str(path), "status": "unknown", "reason": "WAL file absent"})
        return result

    @staticmethod
    def _load(text: str) -> dict[str, Any]:
        values = [float(v) for v in text.split()[:3]]
        if len(values) != 3 or any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Invalid load averages")
        return dict(zip(("load_1m", "load_5m", "load_15m"), values))

    @staticmethod
    def _memory(text: str) -> dict[str, Any]:
        fields = {}
        for line in text.splitlines():
            key, _, raw = line.partition(":")
            if key in {"MemTotal", "MemAvailable", "Dirty", "Writeback"}:
                words = raw.split()
                if len(words) != 2 or words[1] != "kB":
                    raise ValueError("Invalid memory unit")
                fields[key] = int(words[0]) * 1024
        total, available = fields["MemTotal"], fields["MemAvailable"]
        if total <= 0 or not 0 <= available <= total or any(v < 0 for v in fields.values()):
            raise ValueError("Invalid memory counters")
        return {"memory_total_bytes": total, "memory_available_bytes": available,
                "memory_used_percent": 100 * (1 - available / total),
                "dirty_bytes": fields.get("Dirty"), "writeback_bytes": fields.get("Writeback")}

    @staticmethod
    def _disks(text: str) -> dict[str, Any]:
        disks = {}
        for line in text.splitlines():
            words = line.split()
            if len(words) < 14:
                raise ValueError("Invalid diskstats row")
            counters = [int(v) for v in words[3:]]
            if any(v < 0 for v in counters):
                raise ValueError("Negative disk counter")
            disks[words[2]] = {"reads_completed": counters[0], "writes_completed": counters[4],
                               "io_in_progress": counters[8], "io_time_ms": counters[9]}
        return {"disk_counters": disks}

    @staticmethod
    def _integer(text: str) -> dict[str, Any]:
        value = int(text.strip())
        if not 0 <= value <= 100:
            raise ValueError("Invalid dirty ratio")
        return {"vm_dirty_ratio": value}

    @staticmethod
    def _online(text: str) -> dict[str, Any]:
        cpus = set()
        for group in text.strip().split(","):
            parts = group.split("-")
            if len(parts) not in (1, 2):
                raise ValueError("Invalid CPU range")
            start, end = int(parts[0]), int(parts[-1])
            if not 0 <= start <= end <= 65535:
                raise ValueError("Invalid CPU range")
            cpus.update(range(start, end + 1))
        return {"cpu_online_count": len(cpus)}


class DiagnosticianAgent:
    """Threshold crossings are hypotheses requiring investigation, not causes."""

    def diagnose(self, telemetry: dict[str, Any]) -> list[Diagnosis]:
        metrics = telemetry.get("metrics", {})
        findings = []
        checks = [("memory_used_percent", 90.0, "High memory usage"),
                  ("load_1m", metrics.get("cpu_online_count"), "Elevated load average")]
        for metric, threshold, title in checks:
            observed = metrics.get(metric)
            if isinstance(observed, (int, float)) and threshold is not None and observed > threshold:
                findings.append(Diagnosis(
                    issue_id=f"hypothesis_{metric}", title=title,
                    cause="Unknown; threshold warning is a hypothesis, not causal proof or proof of saturation",
                    confidence=0.0, evidence=[EvidenceItem(metric, observed, threshold, "Observed threshold crossing")],
                    recommended_actions=["Inspect workload and collect interval measurements before changing configuration"]))
        for index, wal in enumerate(metrics.get("wal_files", [])):
            if wal.get("status") == "observed" and wal.get("size_bytes", 0) > 64 * 1024 * 1024:
                findings.append(Diagnosis(f"wal_growth_{index}", "Large WAL file", "Unknown; size is not proof of blocked checkpointing", 0.0,
                    evidence=[EvidenceItem("wal_size_bytes", wal["size_bytes"], 64 * 1024 * 1024, wal["path"])],
                    recommended_actions=["Inspect WAL readers and checkpoint policy; do not truncate automatically"]))
        for index, sensor in enumerate(metrics.get("drive_temperatures", [])):
            if sensor["celsius"] > 70:
                findings.append(Diagnosis(f"drive_heat_{index}", "Drive temperature warning", "Unknown; threshold crossing does not prove thermal throttling", 0.0,
                    evidence=[EvidenceItem("drive_temperature_celsius", sensor["celsius"], 70, sensor["sensor"])],
                    recommended_actions=["Inspect drive cooling and manufacturer temperature limits"]))
        for index, bdi in enumerate(metrics.get("bdi", [])):
            if bdi.get("transport") == "usb" and bdi.get("max_ratio", 0) > 50:
                findings.append(Diagnosis(f"usb_bdi_{index}", "USB writeback budget risk", "Unknown; configured ratio is not proof of dirty-page pressure or saturation", 0.0,
                    evidence=[EvidenceItem("bdi_max_ratio", bdi["max_ratio"], 50, bdi["name"])],
                    recommended_actions=["Measure USB writeback workload before proposing any BDI change"]))
        if telemetry.get("errors"):
            findings.append(Diagnosis("telemetry_unknown", "Incomplete telemetry", "Unknown; source reads failed", 0.0,
                                      evidence=[EvidenceItem("telemetry_errors", telemetry["errors"])],
                                      recommended_actions=["Investigate unavailable telemetry sources"]))
        return findings


class PlannerAgent:
    def __init__(self, max_steps: int = 16, *, report_target: Path | str | None = None):
        self.max_steps = _bound(max_steps, 128, "max_steps")
        self.report_target = str(report_target) if report_target is not None else None

    def plan(self, diagnoses: list[Diagnosis]) -> ExecutionPlan:
        if self.report_target is not None:
            content = {"findings": [{"issue_id": d.issue_id, "title": d.title, "cause": d.cause,
                                      "recommendations": d.recommended_actions} for d in diagnoses]}
            return ExecutionPlan(f"plan_{uuid.uuid4().hex}", title="Persist operational recommendations",
                                 steps=[PlanStep("report", 1, WorkspaceConfigUpdate.name,
                                                 self.report_target, {"content": content},
                                                 risk_tier="moderate", is_reversible=True)],
                                 notes="Dedicated operational artifact only; no host remediation")
        steps = []
        for diagnosis in diagnoses:
            for recommendation in diagnosis.recommended_actions:
                if len(steps) >= self.max_steps:
                    break
                steps.append(PlanStep(f"step_{len(steps)+1}", len(steps)+1, "recommend", "operator",
                                      {"recommendation": recommendation, "issue_id": diagnosis.issue_id}))
            if len(steps) >= self.max_steps:
                break
        return ExecutionPlan(f"plan_{uuid.uuid4().hex}", title="Observation and investigation",
                             steps=steps, notes="Recommendations only; no remediation claimed")


class ExecutorAgent:
    """Only registered handlers run; policy confirmation cannot be bypassed.

    No mutation handlers are installed by default. Successful recommendation
    delivery is not a successful repair. Handler contracts return ExecutionResult.
    """

    def __init__(self, policy: AgentPolicyEngine | None = None,
                 handlers: dict[str, Callable[[PlanStep], ExecutionResult]] | None = None,
                 max_steps: int = 16, *, base_dir: Path | str | None = None):
        self.policy = policy or AgentPolicyEngine()
        self.base_dir = Path(base_dir).resolve() if base_dir is not None else None
        self.actions = ({WorkspaceConfigUpdate.name: WorkspaceConfigUpdate(self.base_dir)} if self.base_dir else {})
        self.receipts = ReceiptStore(self.base_dir) if self.base_dir else None
        self.approvals = ApprovalStore(self.base_dir) if self.base_dir else None
        self.last_receipt = None
        self.max_steps = _bound(max_steps, 128, "max_steps")
        self.handlers = {"recommend": self._recommend}
        self.handlers.update(handlers or {})

    @staticmethod
    def _recommend(step: PlanStep) -> ExecutionResult:
        return ExecutionResult(step.step_id, True, output=str(step.params.get("recommendation", "")))

    def _execute_actions(self, plan, dry_run, autonomous, approvals):
        """Journal before each mutation; failures include rollback evidence, not success."""
        if not self.receipts:
            raise ValueError("Action execution requires an explicit base_dir")
        if dry_run:
            for step in plan.steps:
                step.status = StepStatus.SKIPPED
            plan.status = PlanStatus.DRAFT
            return [ExecutionResult(s.step_id, False, "Dry-run: not executed") for s in plan.steps]
        with self.receipts.state.acquire_lock("action-execution"):
            receipt = self.receipts.begin(plan)
            self.last_receipt = receipt
            results, attempted = [], []
            for step in sorted(plan.steps, key=lambda s: s.order):
                action = self.actions.get(step.action_name)
                evidence = {"step_id": step.step_id, "phase": "blocked", "postcondition": False}
                receipt["steps"].append(evidence)
                try:
                    if (action is None or not self.policy.action_grant(step, autonomous=autonomous)
                            or not self.policy.action_grant(step, autonomous=autonomous, rollback=True)):
                        raise PermissionError("Exact action and rollback policy grant required")
                    if not autonomous and not self.approvals.matches(approvals, plan, step):
                        raise PermissionError("Unexpired operator-issued plan/step approval required")
                    before = action.capture(step)
                    evidence.update(phase="prepared", before=before, action=step.to_dict())
                    self.receipts.save(plan, receipt)
                    attempted.append((step, action, before, evidence))
                    step.status = StepStatus.RUNNING
                    action.apply(step)
                    if not action.verify(step):
                        raise RuntimeError("Exact artifact byte postcondition failed")
                    if any(not prior_action.verify(prior_step)
                           for prior_step, prior_action, _, _ in attempted[:-1]):
                        raise RuntimeError("Later step invalidated an earlier artifact postcondition")
                    evidence.update(phase="verified", postcondition=True)
                    step.status = StepStatus.COMPLETED
                    results.append(ExecutionResult(step.step_id, True, "Exact artifact byte postcondition verified"))
                    self.receipts.save(plan, receipt)
                except BaseException as exc:
                    # Cancellation can arrive after replace: restore captured state before propagating.
                    step.status = StepStatus.FAILED
                    evidence["error"] = f"{type(exc).__name__}: {exc}"
                    results.append(ExecutionResult(step.step_id, False, error=evidence["error"]))
                    for prior, handler, before, entry in reversed(attempted):
                        try:
                            if not self.policy.action_grant(prior, autonomous=autonomous, rollback=True):
                                raise PermissionError("Rollback policy denied")
                            if not handler.rollback(prior, before):
                                raise RuntimeError("Rollback pre-state postcondition failed")
                            prior.status = StepStatus.ROLLED_BACK
                            entry["phase"] = "rolled_back"
                        except Exception as rollback_error:
                            entry.update(phase="rollback_failed", rollback_error=str(rollback_error))
                    receipt["phase"] = ("partial" if any(e["phase"] == "rollback_failed" for e in receipt["steps"]) else "rolled_back")
                    plan.status = PlanStatus.FAILED
                    self.receipts.save(plan, receipt)
                    if not isinstance(exc, Exception):
                        raise
                    return results
            receipt["phase"] = "finished"
            self.receipts.save(plan, receipt)
            plan.status = PlanStatus.COMPLETED
            return results

    def execute(self, plan: ExecutionPlan, dry_run: bool = True,
                autonomous: bool = False, *, approvals=()) -> list[ExecutionResult]:
        if len(plan.steps) > self.max_steps:
            raise ValueError("Plan exceeds max_steps; no handlers invoked")
        plan.dry_run = dry_run
        self.last_receipt = None
        ids = [s.step_id for s in plan.steps]
        if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
            raise ValueError("Missing or duplicate step IDs")
        orders = [s.order for s in plan.steps]
        if len(set(orders)) != len(orders) or any(type(o) is not int or o < 1 for o in orders):
            raise ValueError("Invalid or duplicate step order")
        for step in plan.steps:
            step.status = StepStatus.PENDING
        if any(s.action_name == WorkspaceConfigUpdate.name for s in plan.steps):
            return self._execute_actions(plan, dry_run, autonomous, approvals)
        results = []
        for step in sorted(plan.steps, key=lambda s: s.order):
            decision = self.policy.evaluate_step(step, dry_run=dry_run, autonomous=autonomous)
            handler = self.handlers.get(step.action_name)
            if dry_run:
                step.status = StepStatus.SKIPPED
                result = ExecutionResult(step.step_id, False, output="Dry-run: not executed",
                                         error=None if decision.allowed else decision.reason)
            elif not decision.allowed or decision.requires_confirmation or handler is None:
                step.status = StepStatus.SKIPPED
                result = ExecutionResult(step.step_id, False,
                                         error=decision.reason if handler is not None else "No registered handler")
            else:
                start = time.monotonic()
                step.status = StepStatus.RUNNING
                try:
                    result = handler(step)
                    if not isinstance(result, ExecutionResult) or result.step_id != step.step_id:
                        raise ValueError("Handler returned an invalid ExecutionResult")
                    step.status = StepStatus.COMPLETED if result.success else StepStatus.FAILED
                except Exception as exc:
                    step.status = StepStatus.FAILED
                    result = ExecutionResult(step.step_id, False, error=f"{type(exc).__name__}: {exc}")
                result.duration_ms = (time.monotonic() - start) * 1000
            results.append(result)
            if not dry_run and not result.success:
                # Blocked dependencies are as unsafe to continue after as failed ones.
                break
        if dry_run:
            plan.status = PlanStatus.DRAFT
        elif any(s.status == StepStatus.FAILED for s in plan.steps):
            plan.status = PlanStatus.FAILED
        elif plan.steps and all(s.status == StepStatus.COMPLETED for s in plan.steps):
            plan.status = PlanStatus.COMPLETED
        else:
            plan.status = PlanStatus.PENDING_APPROVAL
        return results


class VerifierAgent:
    def verify(self, plan: ExecutionPlan, results: list[ExecutionResult],
               before: dict[str, Any], after: dict[str, Any]) -> VerificationResult:
        # Observation/recommendation delivery cannot establish remediation efficacy.
        details = ("Dry-run: no real actions executed; remediation unverified" if plan.dry_run else
                   "No remediation-specific postcondition evidence; remediation unverified")
        return VerificationResult(plan.plan_id, False, before.get("metrics", {}),
                                  after.get("metrics", {}), details)


class HAOSOrchestrator:
    """One bounded cycle per call, persisted without injecting conversation memory."""

    def __init__(self, base_dir: Path | str | None = None, *, observer=None, planner=None,
                 executor=None, verifier=None, diagnostician=None, max_steps: int = 16, max_cycles: int = 10):
        self.max_steps = _bound(max_steps, 128, "max_steps")
        self.max_cycles = _bound(max_cycles, 100, "max_cycles")
        self.cycles_run = 0
        if base_dir is None:
            from hermes_constants import get_hermes_home
            base_dir = Path(get_hermes_home()) / "agent"
        self.base_dir = Path(base_dir).resolve()
        self.state = HAOSStateManager(self.base_dir)
        self.memory = OperationalMemoryStore(self.base_dir)
        self.bus = HAOSEventBus(log_dir=self.base_dir)
        self.observer = observer or ObserverAgent(wal_paths=[self.base_dir.parent / "state.db-wal"])
        if diagnostician is None:
            from .hierarchy import HierarchicalDiagnostician
            diagnostician = HierarchicalDiagnostician()
        self.diagnostician = diagnostician
        self.planner = planner or PlannerAgent(max_steps)
        self.executor = executor or ExecutorAgent(AgentPolicyEngine(self.base_dir / "agent-policy.yaml"), max_steps=max_steps, base_dir=self.base_dir)
        self.verifier = verifier or VerifierAgent()

    def load_plan(self, plan_id: str) -> ExecutionPlan:
        if not self.state._safe_name(plan_id):
            raise ValueError("Unsafe plan_id")
        path = self.base_dir / "plans" / f"{plan_id}.json"
        self.state._check_path(path)
        import json
        plan = ExecutionPlan.from_dict(json.loads(path.read_text())["plan"])
        if plan.plan_id != plan_id:
            raise ValueError("Stored plan ID does not match its filename")
        return plan

    def grant(self, plan_id: str, step_id: str, *, autonomous: bool = False):
        """Authenticated operator surface only: exact artifact intent, never host capabilities."""
        if type(autonomous) is not bool:
            raise ValueError("autonomous must be boolean")
        with self.state.acquire_lock("policy-grant"):
            plan = self.load_plan(plan_id)
            steps = [s for s in plan.steps if s.step_id == step_id]
            if len(steps) != 1 or steps[0].action_name != WorkspaceConfigUpdate.name:
                raise ValueError("Grant requires one supported artifact step")
            step = steps[0]
            WorkspaceConfigUpdate(self.base_dir).validate(step)
            policy = AgentPolicyEngine(self.base_dir / "agent-policy.yaml")
            if policy._load_failed:
                raise ValueError("Policy load failed")
            grant = {"action": step.action_name, "target": step.target, "params": step.params,
                     "autonomous": autonomous, "allow_rollback": True}
            grants = policy.to_dict()["action_grants"]
            if grant not in grants:
                if len(grants) >= 128:
                    raise ValueError("Exact grant budget exhausted")
                grants.append(grant)
                policy.update_policy({"action_grants": grants}, persist=True)
            self.executor.policy = policy
            return grant

    def approve(self, plan_id: str, step_id: str, *, ttl_seconds: float = 300):
        """Call only after authenticated operator consent, never from a model tool."""
        plan = self.load_plan(plan_id)
        steps = [s for s in plan.steps if s.step_id == step_id]
        if len(steps) != 1:
            raise ValueError("Unknown or duplicate step")
        return ApprovalStore(self.base_dir).issue(plan, steps[0], ttl_seconds=ttl_seconds)

    def run_cycle(self, dry_run: bool = True, autonomous: bool = False, *, mode=None,
                  approvals=(), plan: ExecutionPlan | None = None) -> dict[str, Any]:
        if mode is not None:
            if mode not in {"dry_run", "assisted", "autonomous"}:
                raise ValueError("Unknown execution mode")
            dry_run, autonomous = mode == "dry_run", mode == "autonomous"
        with self.state.acquire_lock("pipeline-cycle"):
            if self.cycles_run >= self.max_cycles:
                raise RuntimeError("Cycle budget exhausted")
            self.cycles_run += 1
            cycle_id = f"cycle_{uuid.uuid4().hex}"
            before = self.observer.observe()
            try:
                diagnoses = self.diagnostician.diagnose(before)
            except Exception as exc:
                hierarchy = getattr(self.diagnostician, "last_result", None)
                failure = {"cycle_id": cycle_id, "phase": "failed", "outcome": "hierarchy_failed",
                           "error": f"{type(exc).__name__}: {exc}",
                           "hierarchy": hierarchy.to_dict() if hierarchy else None,
                           "execution_results": [], "dry_run": dry_run}
                self.state.set_state(failure, merge=False)
                self.state._write_json_file_atomic(self.base_dir / "cycles" / f"{cycle_id}.json", failure)
                raise
            plan = plan or self.planner.plan(diagnoses)
            if len(plan.steps) > self.max_steps:
                raise ValueError("Plan exceeds orchestrator max_steps")
            if not plan.plan_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in plan.plan_id):
                raise ValueError("Unsafe plan_id")
            plan.dry_run = dry_run
            # Persist intent before handlers so failures do not erase the planned operation.
            self.state.set_state({"cycle_id": cycle_id, "phase": "planned", "plan": plan.to_dict()}, merge=False)
            self.bus.publish(HAOSEvent("pipeline.cycle.planned", "HAOSOrchestrator",
                                      payload={"cycle_id": cycle_id, "plan": plan.to_dict()}))
            try:
                results = self.executor.execute(plan, dry_run=dry_run, autonomous=autonomous, approvals=approvals)
            except Exception as exc:
                failure = {"cycle_id": cycle_id, "phase": "failed", "outcome": "execution_blocked",
                           "plan": plan.to_dict(), "dry_run": dry_run,
                           "error": f"{type(exc).__name__}: {exc}",
                           "receipt": getattr(self.executor, "last_receipt", None)}
                self.state.set_state(failure, merge=False)
                self.state._write_json_file_atomic(self.base_dir / "cycles" / f"{cycle_id}.json", failure)
                raise
            after = before if dry_run else self.observer.observe()
            if dry_run:
                verification = VerificationResult(plan.plan_id, False, details="Dry-run: no verification invoked")
            else:
                verification = self.verifier.verify(plan, results, before, after)
                receipt = getattr(self.executor, "last_receipt", None)
                if receipt:
                    verification.verified = (receipt["phase"] == "finished" and bool(receipt["steps"])
                                             and all(s["postcondition"] for s in receipt["steps"]))
                    verification.details = ("Exact operational artifact postconditions verified; no host remediation claimed"
                                            if verification.verified else "Execution failed; inspect rollback receipt")
            if dry_run:
                # Keep the public audit truthful even with an injected verifier/executor.
                verification.verified = False
                verification.details = "Dry-run: no real actions executed; remediation unverified"
                plan.status = PlanStatus.DRAFT
                for step in plan.steps:
                    step.status = StepStatus.SKIPPED
                for result in results:
                    result.success = False
                    result.output = "Dry-run: not executed"
            outcome = "dry_run" if dry_run else ("verified" if verification.verified else "unverified")
            hierarchy = getattr(self.diagnostician, "last_result", None)
            record = {"cycle_id": cycle_id, "phase": "finished", "dry_run": dry_run,
                      "hierarchy": hierarchy.to_dict() if hierarchy else None,
                      "receipt": getattr(self.executor, "last_receipt", None),
                      "autonomous": autonomous, "telemetry_before": before, "telemetry_after": after,
                      "diagnoses": [d.to_dict() for d in diagnoses], "plan": plan.to_dict(),
                      "execution_results": [r.to_dict() for r in results],
                      "verification": verification.to_dict(), "outcome": outcome}
            self.state.set_state(record, merge=False)
            self.state._write_json_file_atomic(self.base_dir / "plans" / f"{plan.plan_id}.json", record)
            self.memory.record_decision(plan.title, plan.notes, impact=verification.details,
                                        metadata={"cycle_id": cycle_id, "outcome": outcome})
            self.memory.update_current_summary(verification.details, issues=[d.title for d in diagnoses])
            self.bus.publish(HAOSEvent("pipeline.cycle.finished", "HAOSOrchestrator", payload=record))
            return record
