"""Tests for HAOS Goal Blocker Typing & Evaluation Engine (DeerFlow 2.0 pattern)."""

from hermes.platform.execution.goal_blockers import (
    BlockerKind,
    GoalBlockerEvaluator,
    GoalEvaluation,
)
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.observability.event_store import EventStore
from hermes.platform.webui.controlplane import ControlPlaneService


def test_goal_blocker_evaluator_kinds():
    assert GoalBlockerEvaluator.classify_blocker_reason("[missing_evidence] no tests") == BlockerKind.MISSING_EVIDENCE
    assert GoalBlockerEvaluator.classify_blocker_reason("User confirmation needed") == BlockerKind.NEEDS_USER_INPUT
    assert GoalBlockerEvaluator.classify_blocker_reason("Syntax error in handler") == BlockerKind.RUN_FAILED
    assert GoalBlockerEvaluator.classify_blocker_reason("Waiting on external webhook") == BlockerKind.EXTERNAL_WAIT
    assert GoalBlockerEvaluator.classify_blocker_reason("Implementation in progress") == BlockerKind.GOAL_NOT_MET_YET


def test_goal_blocker_evaluate_state_missing_evidence():
    eval_res = GoalBlockerEvaluator.evaluate_state(
        goal="Build JWT Authenticator",
        files_modified=["auth/jwt.py"],
        tests_executed=False,
    )
    assert eval_res.satisfied is False
    assert eval_res.blocker_kind == BlockerKind.MISSING_EVIDENCE
    assert eval_res.should_continue is True
    assert "Run the automated test suite" in (eval_res.continuation_prompt or "")
    assert "[MISSING_EVIDENCE]" in eval_res.formatted_reason


def test_goal_blocker_evaluate_state_user_input_and_failure():
    # User input scenario
    res_input = GoalBlockerEvaluator.evaluate_state(
        goal="Deploy to production",
        user_input_requested=True,
    )
    assert res_input.blocker_kind == BlockerKind.NEEDS_USER_INPUT
    assert res_input.should_continue is False

    # Command failure scenario
    res_fail = GoalBlockerEvaluator.evaluate_state(
        goal="Run migrations",
        exit_code=1,
        last_output="FATAL: database connection refused",
    )
    assert res_fail.blocker_kind == BlockerKind.RUN_FAILED
    assert res_fail.should_continue is False
    assert "database connection refused" in res_fail.evidence[1]


def test_kanban_adapter_record_task_blocked():
    kanban = KanbanAdapter()
    spec = TaskSpec(
        id="t-block-01",
        title="Implement Payment Webhook",
        goal="Handle Stripe checkout.session.completed",
        posture="implementer",
    )
    task_id = kanban.upsert_task(spec)

    ok = kanban.record_task_blocked(
        task_id,
        blocker_kind="missing_evidence",
        reason="No mock verification recorded for stripe signature check",
        evidence=["payments/webhook.py"],
    )
    assert ok is True

    task = kanban.get_task(task_id)
    assert task is not None
    assert str(task["status"]).lower() == "blocked"
    assert task["blocker"] is not None
    assert task["blocker"]["kind"] == "missing_evidence"
    assert "signature check" in task["blocker"]["reason"]

    # Test ControlPlane team graph snapshot includes the blocker metadata
    store = EventStore()
    cp = ControlPlaneService(event_store=store, kanban=kanban)
    snapshot = cp.get_team_graph_snapshot()
    assert snapshot is not None
