"""Eval harness: structural leak closure, nop/oracle gate, canary GUID.

Contracts pinned here:
(a) ``run_fn`` receives an ``EvalCaseView`` — id/input/tags visible,
    ``expected`` structurally absent (the executor cannot grade itself
    against the answer).
(b) an optional ``verifier(case, execution)`` sees the full case and its
    verdict overrides the executor's self-claim; a verifier exception fails
    closed.
(c) ``validate_suite``: a no-op that PASSES → ``not_measurable``; an oracle
    that FAILS → ``broken_oracle``; nop rejected + oracle passed →
    ``measurable``; nop rejected without oracle → ``nop_rejected``.
(d) golden tasks: the mock executor is labelled unmeasured and refused as a
    baseline; the artifact verifier rejects an execution that produces
    nothing; ``validate_golden_tasks`` reports the honest status quo.
(e) the canary GUID rides every serialized eval artifact.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from hermes.platform.evals.baselines import BaselineStore
from hermes.platform.evals.golden_tasks import (
    GOLDEN_TASKS,
    GoldenTaskResult,
    artifact_verifier,
    golden_to_eval_suite,
    run_benchmark_and_record,
    validate_golden_tasks,
)
from hermes.platform.evals.runner import (
    EVAL_CANARY_GUID,
    EvalCase,
    EvalCaseView,
    EvalRunner,
    EvalSuite,
    validate_suite,
)


def _suite() -> EvalSuite:
    return EvalSuite(
        id="s1",
        cases=[
            EvalCase(id="c1", input={"q": "1+1"}, expected={"answer": "2"}, tags=["math"]),
            EvalCase(id="c2", input={"q": "2+2"}, expected={"answer": "4"}, tags=["math"]),
        ],
    )


# ---------------------------------------------------------------- point 1

def test_run_fn_never_sees_expected():
    seen = []

    def run_fn(view):
        seen.append(view)
        assert not hasattr(view, "expected"), "executor must not see the answer key"
        return {"passed": True, "score": 1.0}

    res = EvalRunner().run_suite(_suite(), run_fn)
    assert res.pass_rate == 1.0
    assert len(seen) == 2
    assert all(isinstance(v, EvalCaseView) for v in seen)
    assert seen[0].id == "c1" and seen[0].input == {"q": "1+1"}
    assert seen[0].tags == ["math"]


def test_view_is_frozen():
    view = EvalCaseView(id="c", input={}, tags=[])
    with pytest.raises(Exception):
        view.id = "other"  # type: ignore[misc]


def test_executor_exception_is_contained():
    def boom(view):
        raise RuntimeError("executor died")

    res = EvalRunner().run_suite(_suite(), boom)
    assert res.pass_rate == 0.0
    assert all("error" in o["meta"] for o in res.outcomes)


# ---------------------------------------------------------------- point 2 (verifier)

def test_verifier_sees_full_case_and_overrides_self_claim():
    # Executor CLAIMS success; the verifier is the only party with `expected`.
    def run_fn(view):
        return {"output": str(view.input["q"])}

    def verifier(case, execution):
        ok = case.expected["answer"] == {"1+1": "2", "2+2": "4"}[case.input["q"]]
        return {"passed": ok, "score": 1.0 if ok else 0.0, "meta": {}}

    res = EvalRunner().run_suite(_suite(), run_fn, verifier=verifier)
    assert res.pass_rate == 1.0
    # A lying executor is overridden: self-claim "passed" is ignored.
    res2 = EvalRunner().run_suite(
        _suite(), lambda v: {"passed": True, "score": 1.0}, verifier=
        lambda case, ex: {"passed": False, "score": 0.0}
    )
    assert res2.pass_rate == 0.0


def test_verifier_exception_fails_closed():
    def bad_verifier(case, execution):
        raise ValueError("verifier exploded")

    res = EvalRunner().run_suite(_suite(), lambda v: {"passed": True}, verifier=bad_verifier)
    assert res.pass_rate == 0.0
    assert all("verifier_error" in o["meta"] for o in res.outcomes)


# ---------------------------------------------------------------- point 2 (gate)

def _answer_verifier(case, execution):
    return {"passed": execution.get("answer") == case.expected["answer"], "score": 0.0}


def test_validate_suite_flags_nop_that_passes():
    # Verifier that accepts anything → nop passes → suite measures nothing.
    report = validate_suite(
        _suite(),
        verifier=lambda case, ex: {"passed": True},
        nop_fn=lambda view: {},
    )
    assert report["measurable"] is False
    assert {c["verdict"] for c in report["cases"]} == {"not_measurable"}


def test_validate_suite_clean_suite_with_oracle_is_measurable():
    report = validate_suite(
        _suite(),
        verifier=_answer_verifier,
        nop_fn=lambda view: {"answer": "WRONG"},
        oracle_fn=lambda view: {"answer": {"1+1": "2", "2+2": "4"}[view.input["q"]]},
    )
    assert report["measurable"] is True
    assert report["oracle_proved"] is True
    assert {c["verdict"] for c in report["cases"]} == {"measurable"}


def test_validate_suite_broken_oracle():
    report = validate_suite(
        _suite(),
        verifier=_answer_verifier,
        nop_fn=lambda view: {"answer": "WRONG"},
        oracle_fn=lambda view: {"answer": "ALSO-WRONG"},
    )
    assert report["measurable"] is False
    assert {c["verdict"] for c in report["cases"]} == {"broken_oracle"}


def test_validate_suite_without_oracle_leaves_solvability_unproven():
    report = validate_suite(
        _suite(),
        verifier=_answer_verifier,
        nop_fn=lambda view: {"answer": "WRONG"},
    )
    assert report["measurable"] is True
    assert report["oracle_proved"] is False
    assert {c["verdict"] for c in report["cases"]} == {"nop_rejected"}


def test_validate_suite_empty_suite_is_not_measurable():
    report = validate_suite(
        EvalSuite(id="empty"),
        verifier=_answer_verifier,
        nop_fn=lambda view: {},
    )
    assert report["measurable"] is False  # vacuous suite proves nothing


# ---------------------------------------------------------------- point 2 (golden bridge)

def test_golden_to_eval_suite_shape():
    suite = golden_to_eval_suite()
    assert [c.id for c in suite.cases] == list(GOLDEN_TASKS.keys())
    for case in suite.cases:
        # answer key lives ONLY in expected — never in the executor-visible input
        assert "deterministic_assertions" not in case.input
        assert case.expected["assertions"] == GOLDEN_TASKS[case.id].deterministic_assertions
        assert case.expected["artifacts"] == GOLDEN_TASKS[case.id].expected_artifacts


def test_artifact_verifier_requires_evidence():
    case = golden_to_eval_suite(["G001"]).cases[0]
    spec = GOLDEN_TASKS["G001"]
    # nop: produced nothing → rejected
    nop = artifact_verifier(case, {"artifacts": [], "assertions_passed": [], "output": ""})
    assert nop["passed"] is False
    # full claim → accepted
    full = artifact_verifier(
        case,
        {"artifacts": list(spec.expected_artifacts),
         "assertions_passed": list(spec.deterministic_assertions),
         "output": ""},
    )
    assert full["passed"] is True and full["score"] == 1.0
    # partial → fail-closed with the gap named
    part = artifact_verifier(case, {"artifacts": ["patch.diff"], "assertions_passed": []})
    assert part["passed"] is False
    assert part["meta"]["missing_artifacts"] == ["test_pagination.py"]
    assert set(part["meta"]["unclaimed_assertions"]) == set(spec.deterministic_assertions)


def test_validate_golden_tasks_status_quo():
    # Today: the nop (produces nothing) is rejected by the artifact verifier for
    # every task → the suite is measurable; no real oracle exists yet →
    # solvability unproven. Honest, not flattering.
    report = validate_golden_tasks()
    assert report["measurable"] is True
    assert report["oracle_proved"] is False
    assert {c["verdict"] for c in report["cases"]} == {"nop_rejected"}
    assert len(report["cases"]) == 10


def test_validate_golden_tasks_mock_executor_is_broken_oracle():
    # The built-in mock claims success but produces no artifacts/assertions:
    # wired as an "oracle", the gate exposes it as exactly what it is.
    def mock_as_oracle(view):
        return {"artifacts": [], "assertions_passed": []}

    report = validate_golden_tasks(oracle_fn=mock_as_oracle)
    assert report["measurable"] is False
    assert {c["verdict"] for c in report["cases"]} == {"broken_oracle"}


# ---------------------------------------------------------------- point 2 (baseline honesty)

def test_run_benchmark_mock_refuses_baseline(tmp_path):
    store = BaselineStore(db_path=str(tmp_path / "baselines.db"))
    metrics = run_benchmark_and_record(task_ids=["G001", "G002"], label="ci", baseline_store=store)
    assert metrics["measured"] is False
    assert metrics["executor"] == "mock_default"
    assert metrics["baseline_recorded"] is False
    assert "baseline_refused" in metrics
    assert store.latest(suite_id="golden_tasks", label="ci") is None


def test_run_benchmark_real_executor_records_baseline(tmp_path):
    store = BaselineStore(db_path=str(tmp_path / "baselines.db"))

    def real(spec):
        return GoldenTaskResult(task_id=spec.id, success=True, tokens_consumed=100,
                                duration_sec=0.1, assertions_passed=0, assertions_total=0)

    metrics = run_benchmark_and_record(
        task_ids=["G001"], label="ci", baseline_store=store, task_executor=real
    )
    assert metrics["measured"] is True
    assert metrics["baseline_recorded"] is True
    assert store.latest(suite_id="golden_tasks", label="ci") is not None


def test_golden_executor_view_strips_answer_key():
    spec = GOLDEN_TASKS["G001"]
    view = spec.executor_view()
    assert view.deterministic_assertions == []
    assert view.prompt == spec.prompt  # instruction stays visible
    assert view.expected_artifacts == spec.expected_artifacts  # deliverables = instruction


# ---------------------------------------------------------------- point 4 (canary)

def test_canary_in_eval_result_dict():
    res = EvalRunner().run_suite(_suite(), lambda v: {"passed": True})
    assert res.to_dict()["canary"] == EVAL_CANARY_GUID


def test_canary_in_gate_and_metrics():
    assert validate_suite(_suite(), verifier=_answer_verifier,
                          nop_fn=lambda v: {}).get("canary") == EVAL_CANARY_GUID
    assert run_benchmark_and_record(task_ids=["G001"])["canary"] == EVAL_CANARY_GUID
    assert "TRAINING CORPORA" in EVAL_CANARY_GUID
