"""M7: execute_code refunds are capped per turn — a pure-execute_code loop terminates.

IterationBudget is rebuilt every turn, so MAX_REFUNDS_PER_TURN is effectively a
per-turn cap: after it is exhausted, execute_code iterations consume the real
budget and the turn loop always terminates, even with max_iterations=unlimited.
"""
from __future__ import annotations

import sys

from agent.iteration_budget import IterationBudget, MAX_REFUNDS_PER_TURN


def test_refund_cap_per_turn():
    budget = IterationBudget(1000)
    applied = 0
    for _ in range(50):
        assert budget.consume()
        if budget.refund():
            applied += 1
    assert applied == MAX_REFUNDS_PER_TURN == 10
    assert budget.used == 50 - 10
    assert budget.refunds_used == 10


def test_pure_execute_code_loop_terminates():
    """Simulates turn_tool_round's refund on an execute_code-only iteration."""
    budget = IterationBudget(25)
    iterations = 0
    while budget.consume():
        budget.refund()  # the M7 bug: this used to be unbounded
        iterations += 1
        assert iterations < 1000, "execute_code-only loop did not terminate"
    # 10 refunded iterations + 25 consuming iterations
    assert iterations == 10 + 25


def test_refund_never_drives_used_negative():
    budget = IterationBudget(5)
    assert budget.refund() is False
    assert budget.used == 0
    assert budget.consume()
    assert budget.refund() is True
    assert budget.used == 0


def test_cap_is_configurable():
    budget = IterationBudget(100, max_refunds=3)
    applied = sum(1 for _ in range(10) if (budget.consume(), budget.refund())[1])
    assert applied == 3


def test_unlimited_budget_still_terminates_with_finite_cap():
    """Even with max_total=sys.maxsize, refunds stop — the loop's other brake
    (api_call_count < max_iterations) is finite in every real configuration."""
    budget = IterationBudget(sys.maxsize)
    for _ in range(30):
        assert budget.consume()
        budget.refund()
    assert budget.refunds_used == MAX_REFUNDS_PER_TURN
    assert budget.used == 30 - MAX_REFUNDS_PER_TURN
