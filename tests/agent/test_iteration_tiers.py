"""Tests covering the 3-Tier iteration budget progression and coordinator handoff.

Tier 1 (Normal Execution): 0 up to <60% of iterations.
Tier 2 (60% Steer Warning): Triggers at 60% of iterations, idempotent via _steer_60_warned.
Tier 3 (Budget Exhaustion / Handoff): MAX_ITERATIONS_SUMMARY_REQUEST prompts for summary
and evaluation of continuation vs coordinator/operator handoff.
"""

from types import SimpleNamespace
from agent.context_compressor import MAX_ITERATIONS_SUMMARY_REQUEST
from agent.iteration_budget import IterationBudget
from agent.turn_iteration_prep import (
    _maybe_inject_iteration_budget_warning,
    ITERATION_TIER2_STEER_60_WARNING_TEMPLATE,
    ITERATION_BUDGET_WARNING_TEMPLATE,
)


def _make_dummy_agent(used: int, max_total: int, **kwargs):
    budget = IterationBudget(max_total=max_total)
    for _ in range(used):
        budget.consume()
    agent = SimpleNamespace(
        iteration_budget=budget,
        _interrupt_requested=False,
        _steer_60_warned=False,
        _iteration_budget_warning_injected=False,
        budget_warning_ratio=None,
        valid_tool_names=(),
        **kwargs,
    )
    return agent


def test_tier1_no_warning_below_60_percent():
    # max_total = 100, used = 50 (< 60%)
    agent = _make_dummy_agent(used=50, max_total=100)
    messages = [{"role": "tool", "content": "tool output"}]

    injected = _maybe_inject_iteration_budget_warning(agent, messages)
    assert not injected
    assert not agent._steer_60_warned
    assert messages[0]["content"] == "tool output"


def test_tier2_steer_60_warning_triggers_and_is_idempotent():
    # max_total = 100, 60% is 60.
    agent = _make_dummy_agent(used=60, max_total=100)
    messages = [{"role": "tool", "content": "tool output"}]

    injected = _maybe_inject_iteration_budget_warning(agent, messages)
    assert injected
    assert agent._steer_60_warned
    expected_notice = ITERATION_TIER2_STEER_60_WARNING_TEMPLATE.format(
        used=60, maximum=100
    )
    assert expected_notice in messages[0]["content"]
    assert "hyper-efficient" in messages[0]["content"]
    assert "Tier 2" in messages[0]["content"]

    # Subsequent iteration at used = 61: should not inject Tier 2 warning again
    agent.iteration_budget.consume()
    assert agent.iteration_budget.used == 61
    messages2 = [{"role": "tool", "content": "tool output 2"}]
    injected2 = _maybe_inject_iteration_budget_warning(agent, messages2)
    assert not injected2
    assert messages2[0]["content"] == "tool output 2"


def test_tier2_special_threshold_for_approx_67_iterations():
    # When max_total = 67, 40 iterations is the threshold (~59.7%)
    agent = _make_dummy_agent(used=40, max_total=67)
    messages = [{"role": "tool", "content": "initial"}]

    injected = _maybe_inject_iteration_budget_warning(agent, messages)
    assert injected
    assert agent._steer_60_warned
    assert "40 of 67" in messages[0]["content"]


def test_tier3_max_iterations_summary_request_content():
    # Verify Tier 3 MAX_ITERATIONS_SUMMARY_REQUEST requires summary and coordinator handoff evaluation
    assert "maximum number of tool-calling iterations allowed" in MAX_ITERATIONS_SUMMARY_REQUEST
    assert "final response" in MAX_ITERATIONS_SUMMARY_REQUEST
    assert "coordinator/operator" in MAX_ITERATIONS_SUMMARY_REQUEST
    assert "intervention" in MAX_ITERATIONS_SUMMARY_REQUEST
    assert "continuing this session with further iterations is strictly necessary" in MAX_ITERATIONS_SUMMARY_REQUEST
