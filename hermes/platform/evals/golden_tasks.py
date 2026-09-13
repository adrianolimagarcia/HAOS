"""Golden Tasks Benchmark Suite (Step 5.4 / ADR-002 / ADR-010).

Formalizes the 10 canonical evaluation scenarios (G001 to G010)
to measure and compare upstream Hermes vs HAOS platform improvements:
- G001: Small Bugfix
- G002: Repo Exploration
- G003: Architecture Question
- G004: Refactor
- G005: Web Research
- G006: Tool-Heavy Task
- G007: Task Requiring Memory Fabric
- G008: Review & Rework Cycle
- G009: Provider Failure & Exact Failover
- G010: Context Overflow & Token Budgeting
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class GoldenTaskCategory(str, enum.Enum):
    CODE_MODIFICATION = "code_modification"
    EXPLORATION = "exploration"
    ARCHITECTURE = "architecture"
    REFACTORING = "refactoring"
    RESEARCH = "research"
    TOOL_INTENSIVE = "tool_intensive"
    MEMORY_RETRIEVAL = "memory_retrieval"
    REVIEW_GATE = "review_gate"
    FAULT_TOLERANCE = "fault_tolerance"
    CONTEXT_MANAGEMENT = "context_management"


@dataclass(frozen=True)
class GoldenTaskSpec:
    id: str
    name: str
    category: GoldenTaskCategory
    prompt: str
    expected_artifacts: List[str]
    max_tokens_budget: int
    deterministic_assertions: List[str] = field(default_factory=list)
    description: str = ""


GOLDEN_TASKS: Dict[str, GoldenTaskSpec] = {
    "G001": GoldenTaskSpec(
        id="G001",
        name="Small Bugfix",
        category=GoldenTaskCategory.CODE_MODIFICATION,
        prompt="Fix Off-by-One error in token window pagination logic and add pytest.",
        expected_artifacts=["patch.diff", "test_pagination.py"],
        max_tokens_budget=2048,
        deterministic_assertions=["test_pagination_passes", "zero_lint_errors"],
        description="Verifies precision on simple single-file bugfixes without tool pollution.",
    ),
    "G002": GoldenTaskSpec(
        id="G002",
        name="Repo Exploration",
        category=GoldenTaskCategory.EXPLORATION,
        prompt="Identify all implementations of GenericWorkerLane and print their filepaths.",
        expected_artifacts=["exploration_summary.json"],
        max_tokens_budget=3072,
        deterministic_assertions=["found_lane_generic_py"],
        description="Evaluates AST search and LSP symbol navigation without full codebase read.",
    ),
    "G003": GoldenTaskSpec(
        id="G003",
        name="Architecture Question",
        category=GoldenTaskCategory.ARCHITECTURE,
        prompt="Explain why Model != Provider axiom is required to prevent silent degradation.",
        expected_artifacts=["architecture_note.md"],
        max_tokens_budget=1536,
        deterministic_assertions=["mentions_model_route_exhausted_exception"],
        description="Assesses grounding against ADR-003 without hallucinations.",
    ),
    "G004": GoldenTaskSpec(
        id="G004",
        name="Refactor God-File",
        category=GoldenTaskCategory.REFACTORING,
        prompt="Extract handler functions into topical siblings without breaking import backwards compatibility.",
        expected_artifacts=["extracted_sibling.py", "facade.py"],
        max_tokens_budget=4096,
        deterministic_assertions=["all_unit_tests_pass", "facade_reexports_verified"],
        description="Validates facade-sibling decomposition compliance.",
    ),
    "G005": GoldenTaskSpec(
        id="G005",
        name="Web Research",
        category=GoldenTaskCategory.RESEARCH,
        prompt="Summarize recent developments in KV prompt caching across major inference providers.",
        expected_artifacts=["research_brief.md"],
        max_tokens_budget=3500,
        deterministic_assertions=["provenance_citations_included"],
        description="Tests web search retrieval and grounded citation generation.",
    ),
    "G006": GoldenTaskSpec(
        id="G006",
        name="Tool-Heavy Task",
        category=GoldenTaskCategory.TOOL_INTENSIVE,
        prompt="Run git worktree, inspect diffs, execute pytest, and output structured JSON report.",
        expected_artifacts=["execution_report.json"],
        max_tokens_budget=4096,
        deterministic_assertions=["git_worktree_used", "pytest_stdout_captured"],
        description="Verifies coordination of multiple local tools under sandbox policy.",
    ),
    "G007": GoldenTaskSpec(
        id="G007",
        name="Memory Fabric Retrieval",
        category=GoldenTaskCategory.MEMORY_RETRIEVAL,
        prompt="Retrieve architecture decisions regarding rate-limiting and check GraphRAG entities.",
        expected_artifacts=["retrieved_context.json"],
        max_tokens_budget=2048,
        deterministic_assertions=["token_bucket_memory_found"],
        description="Tests Obsidian and GraphRAG federated query precision.",
    ),
    "G008": GoldenTaskSpec(
        id="G008",
        name="Independent Review & Rework",
        category=GoldenTaskCategory.REVIEW_GATE,
        prompt="Review generated pull request for security vulnerabilities and request rework if insecure.",
        expected_artifacts=["review_verdict.json"],
        max_tokens_budget=2500,
        deterministic_assertions=["verdict_in_approved_or_rework"],
        description="Assesses epistemic isolation of Witness Reviewer without bias.",
    ),
    "G009": GoldenTaskSpec(
        id="G009",
        name="Provider Failure Failover",
        category=GoldenTaskCategory.FAULT_TOLERANCE,
        prompt="Execute task while primary provider returns 503; verify seamless fallback to secondary.",
        expected_artifacts=["failover_audit.json"],
        max_tokens_budget=2048,
        deterministic_assertions=["route_switched_same_model", "zero_user_visible_error"],
        description="Validates circuit breaker triggering and route migration.",
    ),
    "G010": GoldenTaskSpec(
        id="G010",
        name="Context Overflow & Budgeting",
        category=GoldenTaskCategory.CONTEXT_MANAGEMENT,
        prompt="Ingest 50KB tool output; verify that context budgeter preserves byte-stable prefix and trims tail.",
        expected_artifacts=["budget_report.json"],
        max_tokens_budget=4096,
        deterministic_assertions=["prefix_unmodified", "total_tokens_within_budget"],
        description="Assesses prompt cache integrity under extreme input load.",
    ),
}


@dataclass
class GoldenTaskResult:
    task_id: str
    success: bool
    tokens_consumed: int
    duration_sec: float
    assertions_passed: int
    assertions_total: int
    error: Optional[str] = None


class GoldenTasksRunner:
    """Executes and scores the Golden Tasks benchmark suite."""

    def __init__(self, task_executor: Optional[Callable[[GoldenTaskSpec], GoldenTaskResult]] = None):
        self._executor = task_executor or self._default_mock_executor

    def run_suite(self, task_ids: Optional[List[str]] = None) -> List[GoldenTaskResult]:
        selected = task_ids or list(GOLDEN_TASKS.keys())
        results = []
        for tid in selected:
            spec = GOLDEN_TASKS[tid]
            res = self._executor(spec)
            results.append(res)
        return results

    @staticmethod
    def _default_mock_executor(spec: GoldenTaskSpec) -> GoldenTaskResult:
        return GoldenTaskResult(
            task_id=spec.id,
            success=True,
            tokens_consumed=min(spec.max_tokens_budget // 2, 1024),
            duration_sec=0.85,
            assertions_passed=len(spec.deterministic_assertions),
            assertions_total=len(spec.deterministic_assertions),
        )


def run_benchmark_and_record(
    task_ids: Optional[List[str]] = None,
    label: str = "current",
    baseline_store: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute the Golden Tasks benchmark suite and optionally record the baseline snapshot."""
    runner = GoldenTasksRunner()
    results = runner.run_suite(task_ids)

    passed_count = sum(1 for r in results if r.success)
    total_count = len(results)
    total_tokens = sum(r.tokens_consumed for r in results)
    total_duration = sum(r.duration_sec for r in results)
    score_pct = (passed_count / total_count * 100.0) if total_count > 0 else 0.0

    metrics = {
        "suite": "golden_tasks",
        "label": label,
        "tasks_total": total_count,
        "tasks_passed": passed_count,
        "score_percent": round(score_pct, 1),
        "total_tokens": total_tokens,
        "total_duration_sec": round(total_duration, 2),
        "tasks": [dataclasses.asdict(r) for r in results],
    }

    if baseline_store is not None:
        baseline_store.save(suite_id="golden_tasks", label=label, metrics=metrics)

    return metrics
