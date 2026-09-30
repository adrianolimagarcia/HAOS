"""Agent Evaluation Test Harness.

Provides benchmark suites, regression testing, and quantitative metrics:
- completion_rate
- correctness
- cost (USD)
- latency (ms)
- tool_efficiency
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class BenchmarkTask:
    task_id: str
    category: str = "general"
    prompt: str = ""
    expected_output_contains: Optional[str] = None
    verify_fn: Optional[Callable[[Dict[str, Any]], bool]] = None
    max_duration_seconds: float = 30.0
    target_files: List[str] = field(default_factory=list)
    # Flexible compatibility kwargs
    domain: Optional[str] = None
    expected_outcome: Optional[str] = None

    def __post_init__(self) -> None:
        if self.domain and self.category == "general":
            self.category = self.domain
        if self.expected_outcome and not self.expected_output_contains:
            self.expected_output_contains = self.expected_outcome


@dataclass
class BenchmarkTaskResult:
    task_id: str
    completed: bool
    correctness: float  # 0.0 to 1.0
    duration_ms: float
    tokens: int
    cost_usd: float
    tool_calls_count: int
    tool_efficiency: float
    error: Optional[str] = None


@dataclass
class BenchmarkSuiteReport:
    suite_name: str
    total_tasks: int
    completion_rate: float
    avg_correctness: float
    avg_latency_ms: float
    avg_cost_usd: float
    avg_tool_efficiency: float
    results: List[BenchmarkTaskResult] = field(default_factory=list)

    @property
    def passed_tasks(self) -> int:
        return sum(1 for r in self.results if r.completed)

    @property
    def average_latency_ms(self) -> float:
        return self.avg_latency_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "total_tasks": self.total_tasks,
            "completion_rate": round(self.completion_rate, 4),
            "avg_correctness": round(self.avg_correctness, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "avg_cost_usd": round(self.avg_cost_usd, 4),
            "avg_tool_efficiency": round(self.avg_tool_efficiency, 4),
            "results": [r.__dict__ for r in self.results],
        }


class AgentEvaluationHarness:
    """Benchmark runner evaluating agent harness instances."""

    def __init__(self, suite_name: str = "haos-core-eval"):
        self.suite_name = suite_name
        self.tasks: List[BenchmarkTask] = []

    def add_task(self, task: BenchmarkTask) -> None:
        self.tasks.append(task)

    def run_suite(
        self,
        runner_fn: Optional[Callable[[BenchmarkTask], Any]] = None,
        tasks: Optional[List[BenchmarkTask]] = None,
        suite_name: Optional[str] = None,
        agent_executor: Optional[Callable[[BenchmarkTask], Any]] = None,
        **kwargs: Any,
    ) -> BenchmarkSuiteReport:
        """Run all tasks in the benchmark suite and compute aggregated metrics."""
        effective_tasks = tasks if tasks is not None else self.tasks
        effective_suite = suite_name or self.suite_name
        exec_fn = runner_fn or agent_executor or (lambda t: {"completed": True, "output": "ok"})
        results: List[BenchmarkTaskResult] = []

        for task in effective_tasks:
            start = time.time()
            try:
                raw_out = exec_fn(task)
                dur = (time.time() - start) * 1000.0

                if isinstance(raw_out, str):
                    out = {"completed": True, "output": raw_out}
                elif isinstance(raw_out, dict):
                    out = raw_out
                else:
                    out = {"completed": bool(raw_out), "output": str(raw_out)}

                completed = bool(out.get("completed", True))
                correctness = 1.0
                if task.verify_fn:
                    correctness = 1.0 if task.verify_fn(out) else 0.0
                elif task.expected_output_contains:
                    content = str(out.get("output", ""))
                    correctness = 1.0 if task.expected_output_contains in content else 0.0

                tool_calls = out.get("tool_calls", 1)
                tokens = out.get("tokens", 1000)
                cost = out.get("cost_usd", 0.005)

                # Tool efficiency: ideal calls / actual calls
                ideal_calls = max(1, len(task.target_files))
                efficiency = min(1.0, ideal_calls / max(1, tool_calls))

                results.append(BenchmarkTaskResult(
                    task_id=task.task_id,
                    completed=completed,
                    correctness=correctness,
                    duration_ms=dur,
                    tokens=tokens,
                    cost_usd=cost,
                    tool_calls_count=tool_calls,
                    tool_efficiency=efficiency,
                ))
            except Exception as exc:
                dur = (time.time() - start) * 1000.0
                results.append(BenchmarkTaskResult(
                    task_id=task.task_id,
                    completed=False,
                    correctness=0.0,
                    duration_ms=dur,
                    tokens=0,
                    cost_usd=0.0,
                    tool_calls_count=0,
                    tool_efficiency=0.0,
                    error=str(exc),
                ))

        n = len(results)
        if n == 0:
            return BenchmarkSuiteReport(
                suite_name=effective_suite,
                total_tasks=0,
                completion_rate=0.0,
                avg_correctness=0.0,
                avg_latency_ms=0.0,
                avg_cost_usd=0.0,
                avg_tool_efficiency=0.0,
            )

        comp_rate = sum(1 for r in results if r.completed) / n
        avg_corr = sum(r.correctness for r in results) / n
        avg_lat = sum(r.duration_ms for r in results) / n
        avg_cost = sum(r.cost_usd for r in results) / n
        avg_eff = sum(r.tool_efficiency for r in results) / n

        return BenchmarkSuiteReport(
            suite_name=effective_suite,
            total_tasks=n,
            completion_rate=comp_rate,
            avg_correctness=avg_corr,
            avg_latency_ms=avg_lat,
            avg_cost_usd=avg_cost,
            avg_tool_efficiency=avg_eff,
            results=results,
        )
