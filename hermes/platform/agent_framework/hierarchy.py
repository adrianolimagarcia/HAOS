"""Bounded operational diagnosis using canonical TaskSpec dependencies.

Workers are trusted, cancellation-cooperative async callables, not subprocesses or
LLM agents. They may analyze their scoped snapshot and propose actions; this module
never executes remediation. Blocking code/cancellation suppression is outside this
in-process contract (use an isolated executor for untrusted capabilities).
"""
from __future__ import annotations

import asyncio
import copy
import math
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from hermes.platform.tasks.spec import TaskSpec
from .models import Diagnosis, EvidenceItem


DOMAIN_METRICS = {
    "storage": ("disk_counters", "wal_files", "drive_temperatures", "bdi"),
    "memory": ("memory_used_percent", "memory_total_bytes", "memory_available_bytes",
               "dirty_bytes", "writeback_bytes", "vm_dirty_ratio"),
    "system": ("load_1m", "load_5m", "load_15m", "cpu_online_count"),
}
DOMAIN_SOURCES = {
    "storage": ("diskstats", "wal_files", "drive_temperatures", "bdi"),
    "memory": ("meminfo", "vm_dirty_ratio"),
    "system": ("loadavg", "cpu_online"),
}


@dataclass(frozen=True)
class WorkerContext:
    task: TaskSpec
    telemetry: dict[str, Any]
    dependencies: dict[str, tuple[Diagnosis, ...]]
    cancel_event: asyncio.Event
    deadline: float
    max_findings: int


Worker = Callable[[WorkerContext], Awaitable[list[Diagnosis]]]


@dataclass
class TaskResult:
    task_id: str
    domain: str
    status: str
    diagnoses: list[Diagnosis] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "domain": self.domain, "status": self.status,
                "diagnoses": [d.to_dict() for d in self.diagnoses], "error": self.error}


@dataclass
class HierarchyResult:
    status: str
    diagnoses: list[Diagnosis]
    task_results: list[TaskResult]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "diagnoses": [d.to_dict() for d in self.diagnoses],
                "task_results": [r.to_dict() for r in self.task_results]}


class HierarchyExecutionError(RuntimeError):
    """Partial diagnoses cannot make a failed supervisory run look healthy."""

    def __init__(self, result: HierarchyResult):
        self.result = result
        super().__init__(f"Hierarchical diagnosis {result.status}")


def _warning(issue: str, title: str, metric: str, value: Any, threshold: Any,
             description: str, action: str, domain: str) -> Diagnosis:
    return Diagnosis(issue, title, "Unknown; observed warning is not causal proof or proof of saturation", 0.0,
                     [EvidenceItem(metric, value, threshold, description)], [domain], [action])


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


async def memory_worker(context: WorkerContext) -> list[Diagnosis]:
    metrics = context.telemetry["metrics"]
    value = metrics.get("memory_used_percent")
    if _number(value) and 90 < value <= 100:
        return [_warning("hypothesis_memory_used_percent", "High memory usage", "memory_used_percent",
                         value, 90, "Observed threshold crossing", "Inspect workload and collect interval measurements before changing configuration", "memory")]
    return []


async def system_worker(context: WorkerContext) -> list[Diagnosis]:
    metrics = context.telemetry["metrics"]
    load, cpus = metrics.get("load_1m"), metrics.get("cpu_online_count")
    findings = []
    if _number(load) and _number(cpus) and cpus > 0 and load > cpus:
        findings.append(_warning("hypothesis_load_1m", "Elevated load average", "load_1m", load, cpus,
                                 "Observed threshold crossing; load includes uninterruptible tasks",
                                 "Inspect workload and collect interval measurements before changing configuration", "system"))
    return findings


async def storage_worker(context: WorkerContext) -> list[Diagnosis]:
    metrics = context.telemetry["metrics"]
    findings = []
    checks = (
        ("wal_files", "size_bytes", 64 * 1024 * 1024, "wal_growth", "Large WAL file", "path",
         "Inspect WAL readers and checkpoint policy; do not truncate automatically"),
        ("drive_temperatures", "celsius", 70, "drive_heat", "Drive temperature warning", "sensor",
         "Inspect drive cooling and manufacturer temperature limits"),
        ("bdi", "max_ratio", 50, "usb_bdi", "USB writeback budget risk", "name",
         "Measure USB writeback workload before proposing any BDI change"),
    )
    for collection, metric, threshold, prefix, title, source, action in checks:
        for index, item in enumerate(metrics.get(collection, [])):
            if context.cancel_event.is_set():
                raise asyncio.CancelledError
            if collection == "wal_files" and item.get("status") != "observed":
                continue
            if collection == "bdi" and item.get("transport") != "usb":
                continue
            value = item.get(metric)
            if _number(value) and value > threshold:
                findings.append(_warning(f"{prefix}_{index}", title, metric, value, threshold,
                                         str(item.get(source, "Unknown source")), action, "storage"))
                if len(findings) >= context.max_findings:
                    return findings
    return findings


class HierarchicalDiagnostician:
    """Supervisor partitions domains, admits ready tasks, and aggregates in task order.

    Failures block dependent tasks, not independent siblings. Budgets bound admitted
    tasks, active workers, elapsed cooperative execution, and accepted findings.
    No task can spawn children or choose additional capabilities. Timeouts are not
    hard deadlines: blocking/cancellation-suppressing workers violate this trusted
    async contract. cancel_event is asyncio.Event bound to the caller's loop;
    another thread must signal it with loop.call_soon_threadsafe(event.set).
    """

    def __init__(self, workers: dict[str, Worker] | None = None, *, max_parallel: int = 3,
                 max_tasks: int = 16, worker_timeout: float = 5.0,
                 total_timeout: float = 15.0, max_findings: int = 64):
        for name, value, ceiling in (("max_parallel", max_parallel, 16),
                                     ("max_tasks", max_tasks, 128), ("max_findings", max_findings, 1024)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be an integer in [1, {ceiling}]")
        for value in (worker_timeout, total_timeout):
            if not _number(value) or not 0 < value <= 300:
                raise ValueError("timeouts must be finite seconds in (0, 300]")
        self.workers = dict(workers if workers is not None else
                            {"storage": storage_worker, "memory": memory_worker, "system": system_worker})
        if set(self.workers) - set(DOMAIN_METRICS) or not self.workers:
            raise ValueError("workers must bind supported storage/memory/system domains")
        self.max_parallel, self.max_tasks = max_parallel, max_tasks
        self.worker_timeout, self.total_timeout = worker_timeout, total_timeout
        self.max_findings = max_findings
        self.last_result: HierarchyResult | None = None

    def partition(self) -> list[TaskSpec]:
        return [TaskSpec(id=f"diagnose-{domain}", title=f"Diagnose {domain}", goal="Analyze scoped observed telemetry",
                         posture=domain, task_class="ops", risk_level="low", workspace_type="none",
                         parent_id="operational-diagnosis", allow_delegation=False, allow_child_tasks=False,
                         max_runs=1, max_child_tasks=0, max_cost_usd=0)
                for domain in DOMAIN_METRICS if domain in self.workers]

    def diagnose(self, telemetry: dict[str, Any]) -> list[Diagnosis]:
        """Sync pipeline adapter; async consumers must await run() instead."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.last_result = asyncio.run(self.run(telemetry))
            if self.last_result.status != "completed":
                raise HierarchyExecutionError(self.last_result)
            return self.last_result.diagnoses
        raise RuntimeError("Use await HierarchicalDiagnostician.run() inside an event loop")

    def _validate(self, tasks: list[TaskSpec]) -> None:
        if not tasks or len(tasks) > self.max_tasks:
            raise ValueError("task budget exceeded or empty task graph")
        ids = {t.id for t in tasks}
        if len(ids) != len(tasks):
            raise ValueError("duplicate task IDs")
        remaining = {t.id: set(t.requires_tasks) for t in tasks}
        for task in tasks:
            if task.posture not in self.workers or remaining[task.id] - ids:
                raise ValueError("unsupported worker or dependency outside scoped graph")
            # Typed edges cannot silently introduce requirements the scheduler ignores.
            if any(e.source_task != task.id or e.target_task not in task.requires_tasks
                   or e.kind != "requires" for e in task.typed_dependencies):
                raise ValueError("typed dependencies must match scoped requires_tasks")
        while remaining:
            ready = {key for key, deps in remaining.items() if not deps}
            if not ready:
                raise ValueError("cyclic task dependencies")
            remaining = {key: deps - ready for key, deps in remaining.items() if key not in ready}

    def _scope(self, telemetry: dict[str, Any], domain: str) -> dict[str, Any]:
        return copy.deepcopy({"timestamp": telemetry.get("timestamp"),
            "metrics": {k: v for k, v in telemetry.get("metrics", {}).items() if k in DOMAIN_METRICS[domain]},
            "sources": {k: v for k, v in telemetry.get("sources", {}).items() if k in DOMAIN_SOURCES[domain]},
            "errors": {k: v for k, v in telemetry.get("errors", {}).items() if k in DOMAIN_SOURCES[domain]}})

    async def run(self, telemetry: dict[str, Any], *, tasks: list[TaskSpec] | None = None,
                  cancel_event: asyncio.Event | None = None) -> HierarchyResult:
        tasks = copy.deepcopy(tasks if tasks is not None else self.partition())
        self._validate(tasks)
        # External TaskSpecs are dependency descriptions, never delegation authority.
        for task in tasks:
            task.allow_delegation = task.allow_child_tasks = False
            task.max_child_tasks = task.max_cost_usd = 0
            task.max_runs = 1
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.total_timeout
        cancel = cancel_event if cancel_event is not None else asyncio.Event()
        results: dict[str, TaskResult] = {}
        active: dict[str, asyncio.Task] = {}
        pending = {t.id: t for t in tasks}

        async def execute(task: TaskSpec) -> TaskResult:
            domain = task.posture
            scope = self._scope(telemetry, domain)
            context = WorkerContext(task, scope,
                copy.deepcopy({dep: tuple(results[dep].diagnoses) for dep in task.requires_tasks}),
                cancel, min(deadline, loop.time() + self.worker_timeout), self.max_findings)
            try:
                output = await asyncio.wait_for(self.workers[domain](context), self.worker_timeout)
                if not isinstance(output, list) or any(not isinstance(d, Diagnosis) for d in output):
                    raise TypeError("worker must return list[Diagnosis]")
                if len(output) > self.max_findings:
                    raise ValueError("worker finding budget exceeded")
                for diagnosis in output:
                    if not diagnosis.evidence:
                        raise ValueError("worker diagnosis requires evidence")
                errors = scope["errors"]
                if errors:
                    output.append(Diagnosis(f"telemetry_unknown_{domain}", "Incomplete telemetry",
                        "Unknown; source reads failed", 0.0, [EvidenceItem("telemetry_errors", errors)], [domain],
                        ["Investigate unavailable telemetry sources"]))
                return TaskResult(task.id, domain, "completed", copy.deepcopy(output))
            except asyncio.TimeoutError:
                return TaskResult(task.id, domain, "timed_out", error="worker timeout")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return TaskResult(task.id, domain, "failed", error=f"{type(exc).__name__}: {exc}")

        try:
            while pending or active:
                terminal = "cancelled" if cancel.is_set() else ("timed_out" if loop.time() >= deadline else None)
                if terminal:
                    for key, task in pending.items():
                        results[key] = TaskResult(key, task.posture, terminal, error="supervisor " + terminal)
                    pending.clear()
                    for key, future in active.items():
                        future.cancel()
                        results[key] = TaskResult(key, next(t.posture for t in tasks if t.id == key), terminal,
                                                  error="supervisor " + terminal)
                    await asyncio.gather(*active.values(), return_exceptions=True)
                    active.clear()
                    break
                for key, task in list(pending.items()):
                    deps = task.requires_tasks
                    if any(dep in results and results[dep].status != "completed" for dep in deps):
                        results[key] = TaskResult(key, task.posture, "blocked", error="dependency did not complete")
                        del pending[key]
                    elif all(dep in results for dep in deps) and len(active) < self.max_parallel:
                        active[key] = asyncio.create_task(execute(task))
                        del pending[key]
                if active:
                    done, _ = await asyncio.wait(active.values(), timeout=min(0.02, max(0, deadline - loop.time())),
                                                 return_when=asyncio.FIRST_COMPLETED)
                    for key, future in list(active.items()):
                        if future in done:
                            if future.cancelled():
                                results[key] = TaskResult(key, next(t.posture for t in tasks if t.id == key), "cancelled")
                            else:
                                results[key] = future.result()
                            del active[key]
        finally:
            # Caller cancellation must not leave admitted workers running in the background.
            for future in active.values():
                future.cancel()
            await asyncio.gather(*active.values(), return_exceptions=True)
        ordered = [results[t.id] for t in tasks]
        findings = [d for result in ordered for d in result.diagnoses]
        if len(findings) > self.max_findings:
            # A global budget failure is visible; never silently call a truncated result complete.
            findings = findings[:self.max_findings]
            status = "budget_exceeded"
        else:
            statuses = {r.status for r in ordered}
            status = "completed" if statuses == {"completed"} else (
                "cancelled" if "cancelled" in statuses else "timed_out" if "timed_out" in statuses else "failed")
        result = HierarchyResult(status, findings, ordered)
        self.last_result = result
        return result
