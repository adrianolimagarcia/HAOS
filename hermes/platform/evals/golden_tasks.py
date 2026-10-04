"""Golden Tasks Benchmark Suite (Step 5.4 / ADR-002 / GOV-010).

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

from hermes.platform.evals.runner import (
    EVAL_CANARY_GUID,
    EvalCase,
    EvalSuite,
    validate_suite,
)


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

    def executor_view(self) -> "GoldenTaskSpec":
        """Copy of the spec with the answer key stripped.

        What a task EXECUTOR may see: prompt, category, budget. The
        ``deterministic_assertions`` are the verifier's answer key — an
        executor that sees them can grade itself against the answer instead of
        doing the work. ``expected_artifacts`` stays visible: naming the
        deliverables is part of the instruction, not the grading.
        """
        return dataclasses.replace(self, deterministic_assertions=[])


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
        description="Assesses grounding against GOV-004 without hallucinations.",
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
    """Executes and scores the Golden Tasks benchmark suite.

    The default executor is a MOCK: it passes every task without doing any
    work, so its scores measure nothing. It exists only to exercise the
    plumbing (CLI, baseline store). Pass a real ``task_executor`` to get
    numbers worth recording — ``run_benchmark_and_record`` refuses to persist
    a mock run as a baseline.
    """

    def __init__(self, task_executor: Optional[Callable[[GoldenTaskSpec], GoldenTaskResult]] = None):
        self._executor = task_executor or self._default_mock_executor
        self.uses_mock = task_executor is None

    def run_suite(self, task_ids: Optional[List[str]] = None) -> List[GoldenTaskResult]:
        """Execute the selected tasks.

        The executor receives ``spec.executor_view()`` — prompt, budget and
        deliverable names, but NOT the deterministic assertions it will be
        graded on. Self-grading against a hidden answer key is exactly the
        leak the executor/verifier split closes.
        """
        selected = task_ids or list(GOLDEN_TASKS.keys())
        results = []
        for tid in selected:
            spec = GOLDEN_TASKS[tid]
            res = self._executor(spec.executor_view())
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


def golden_to_eval_suite(task_ids: Optional[List[str]] = None) -> EvalSuite:
    """Bridge the Golden Tasks catalog to the EvalRunner format.

    ``input`` carries what an executor may see (prompt, budget, deliverable
    names); ``expected`` carries the answer key (deterministic assertions +
    artifacts) and reaches only a verifier — never the run_fn, which receives
    an ``EvalCaseView``.
    """
    cases: List[EvalCase] = []
    for tid in (task_ids or list(GOLDEN_TASKS.keys())):
        spec = GOLDEN_TASKS[tid]
        cases.append(
            EvalCase(
                id=spec.id,
                input={
                    "prompt": spec.prompt,
                    "category": spec.category.value,
                    "max_tokens_budget": spec.max_tokens_budget,
                    "expected_artifacts": list(spec.expected_artifacts),
                },
                expected={
                    "artifacts": list(spec.expected_artifacts),
                    "assertions": list(spec.deterministic_assertions),
                },
                tags=[spec.category.value],
            )
        )
    return EvalSuite(
        id="golden_tasks",
        description="Canonical G001-G010 scenarios, verifier-gated",
        cases=cases,
    )


def artifact_verifier(case: EvalCase, execution: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic verifier for answer-key cases (see ``golden_to_eval_suite``).

    Reads ``case.expected`` = {"artifacts": [...], "assertions": [...],
    "required_output_substrings": [...]} and judges the executor's execution
    dict {"artifacts": [...], "assertions_passed": [...], "output": str}:
    required artifacts must be present, required substrings must appear in the
    output, and required assertions must be claimed satisfied by the executor.

    Honest limitation: artifact presence and output substrings are EXTERNALLY
    verified; assertion satisfaction is executor-reported evidence (checking
    "test_pagination_passes" for real means running pytest in a sandbox, which
    is the harness's job, not this verifier's). A case with an empty answer key
    can never pass — fail-closed, so ``validate_suite`` flags vacuous cases.
    """
    exp = case.expected if isinstance(case.expected, dict) else {}
    required_artifacts = [str(a) for a in (exp.get("artifacts") or [])]
    required_assertions = [str(a) for a in (exp.get("assertions") or exp.get("deterministic_assertions") or [])]
    required_substrings = [str(s) for s in (exp.get("required_output_substrings") or [])]

    execution = execution if isinstance(execution, dict) else {}
    got_artifacts = {str(a) for a in (execution.get("artifacts") or [])}
    claimed_assertions = {str(a) for a in (execution.get("assertions_passed") or [])}
    output = str(execution.get("output") or "")

    missing_artifacts = [a for a in required_artifacts if a not in got_artifacts]
    unclaimed_assertions = [a for a in required_assertions if a not in claimed_assertions]
    missing_substrings = [s for s in required_substrings if s not in output]

    checks = len(required_artifacts) + len(required_assertions) + len(required_substrings)
    failed = len(missing_artifacts) + len(unclaimed_assertions) + len(missing_substrings)
    passed = checks > 0 and failed == 0
    score = 0.0 if checks == 0 else (checks - failed) / checks
    return {
        "passed": passed,
        "score": round(score, 4),
        "meta": {
            "verifier": "artifact",
            "checks": checks,
            "missing_artifacts": missing_artifacts,
            "unclaimed_assertions": unclaimed_assertions,
            "missing_substrings": missing_substrings,
        },
    }


def _noop_golden_executor(view: Any) -> Dict[str, Any]:
    """The nop probe for ``validate_golden_tasks``: claims nothing, produces nothing."""
    return {"artifacts": [], "assertions_passed": [], "output": ""}


def validate_golden_tasks(
    task_ids: Optional[List[str]] = None,
    oracle_fn: Optional[Callable[[Any], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run the nop/oracle measurability gate over the Golden Tasks suite.

    Every task must REJECT the no-op executor (an execution that produced no
    artifacts and claimed no assertions); with an ``oracle_fn`` supplied, the
    reference executor must additionally PASS every task. Without an oracle,
    nop-rejected cases are ``nop_rejected`` — measurability holds, solvability
    is assumed, not proven.
    """
    return validate_suite(
        golden_to_eval_suite(task_ids),
        verifier=artifact_verifier,
        nop_fn=_noop_golden_executor,
        oracle_fn=oracle_fn,
    )


def run_benchmark_and_record(
    task_ids: Optional[List[str]] = None,
    label: str = "current",
    baseline_store: Optional[Any] = None,
    task_executor: Optional[Callable[[GoldenTaskSpec], GoldenTaskResult]] = None,
) -> Dict[str, Any]:
    """Execute the Golden Tasks benchmark and record a baseline snapshot.

    Honesty contract: a run on the default MOCK executor is labelled
    ``measured: False`` and is NEVER persisted as a baseline — a 100% score
    from an executor that does no work would poison every later comparison.
    Pass ``task_executor`` for numbers worth recording.
    """
    runner = GoldenTasksRunner(task_executor=task_executor)
    results = runner.run_suite(task_ids)

    passed_count = sum(1 for r in results if r.success)
    total_count = len(results)
    total_tokens = sum(r.tokens_consumed for r in results)
    total_duration = sum(r.duration_sec for r in results)
    score_pct = (passed_count / total_count * 100.0) if total_count > 0 else 0.0

    measured = not runner.uses_mock
    metrics = {
        "suite": "golden_tasks",
        "label": label,
        "tasks_total": total_count,
        "tasks_passed": passed_count,
        "score_percent": round(score_pct, 1),
        "total_tokens": total_tokens,
        "total_duration_sec": round(total_duration, 2),
        "executor": "injected" if measured else "mock_default",
        "measured": measured,
        "canary": EVAL_CANARY_GUID,
        "tasks": [dataclasses.asdict(r) for r in results],
    }

    if baseline_store is not None:
        if measured:
            baseline_store.save(suite_id="golden_tasks", label=label, metrics=metrics)
            metrics["baseline_recorded"] = True
        else:
            metrics["baseline_recorded"] = False
            metrics["baseline_refused"] = (
                "mock executor measures nothing (nop passes every task); "
                "refusing to record it as a baseline"
            )

    return metrics
