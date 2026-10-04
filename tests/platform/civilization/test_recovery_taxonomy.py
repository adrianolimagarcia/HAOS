"""ADR-021 — Failure-recovery taxonomy for Civilization delegation.

Covers the five implemented pieces:
1. RecoveryStrategy enum + RecoveryPolicy dataclass defaults.
2. failure_profile(): deterministic replay-based failure counting per
   (bot_id, goal_slug) from the append-only event stream.
3. Revalidation of insufficient DONE: civ.leaf.completed with
   payload status='insufficient', NEUTRAL reputation (delta 0), and
   suggest_recovery treating it as a light failure.
4. Fail-closed ceiling: attempt >= max_retries -> strategy 'halt',
   reason 'max_retries_exceeded'.
5. Rollout flag HAOS_CIV_RECOVERY=0 disables suggestion AND the
   insufficient revalidation (record_task_result/leaf identical to pre-ADR).

Design note (deliberate, documented): an insufficient DONE is NOT
downgraded to civ.leaf.failed — the leaf event keeps its completed name so
existing e2e consumers do not break; only the payload status and the
reputation delta change.
"""
from __future__ import annotations

import pytest

from hermes.platform.civilization.delegation import (
    RecoveryPolicy,
    RecoveryStrategy,
    create_bot,
    failure_profile,
    record_task_result,
    route_task,
    suggest_recovery,
)
from hermes.platform.civilization.feature_flags import reset_feature_flags
from hermes.platform.observability.event_store import EventStore
from hermes.platform.society.manager import SocietyManager

EVENT_RECOVERY = "civ.delegate.recovery"
EVENT_COMPLETED = "civ.leaf.completed"
EVENT_FAILED = "civ.leaf.failed"


@pytest.fixture
def civ_store(tmp_path, monkeypatch):
    """Isolated event store with recovery + civ flags at their defaults (ON)."""
    monkeypatch.delenv("HAOS_CIV_ENABLED", raising=False)
    monkeypatch.delenv("HAOS_CIV_RECOVERY", raising=False)
    monkeypatch.delenv("HAOS_CIV_CANARY_BOTS", raising=False)
    reset_feature_flags()
    store = EventStore(db_path=tmp_path / "events.db")
    yield store
    reset_feature_flags()


def _routed(store, bot_id="worker-bot", goal="Fix flaky integration test"):
    return route_task({"bot_id": bot_id, "goal": goal}, event_store=store)


def _fail(store, task, error="boom"):
    record_task_result(task, {"status": "failed", "summary": "", "error": error},
                       event_store=store)


# ---------------------------------------------------------------------------
# 1. Enum + policy
# ---------------------------------------------------------------------------

def test_strategy_enum_members():
    values = {s.value for s in RecoveryStrategy}
    assert {"retry", "replan", "reassign", "decompose", "create_worker", "halt"} <= values


def test_policy_defaults():
    pol = RecoveryPolicy()
    assert pol.max_retries == 3
    assert pol.enabled_strategies is None  # None -> all strategies
    assert pol.halt_on_max_retries is True


# ---------------------------------------------------------------------------
# 2. failure_profile — replay-based counting
# ---------------------------------------------------------------------------

def test_failure_profile_counts_per_bot_and_goal(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    create_bot("other-bot", "testing", event_store=store)

    _fail(store, _routed(store, "worker-bot", "Fix flaky integration test"))
    _fail(store, _routed(store, "worker-bot", "Fix flaky integration test"))
    _fail(store, _routed(store, "other-bot", "Fix flaky integration test"))
    _fail(store, _routed(store, "worker-bot", "Unrelated goal about kernels"))

    prof = failure_profile(store, "worker-bot", "fix-flaky-integration")
    assert prof["count"] == 2
    assert prof["last_status"] == "failed"
    assert prof["last_strategy"] is None

    # Per-goal guard: a different goal for the same bot does not share the count.
    other = failure_profile(store, "worker-bot", "unrelated-goal-about")
    assert other["count"] == 1

    # Successes never count as failures.
    ok_task = _routed(store, "worker-bot", "Ship docs")
    record_task_result(ok_task, {"status": "completed",
                                 "summary": "Docs shipped and verified end to end"},
                       event_store=store)
    assert failure_profile(store, "worker-bot", "ship-docs")["count"] == 0


def test_failure_profile_replay_is_deterministic(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    task = _routed(store)
    _fail(store, task)
    suggest_recovery(task, {"status": "failed", "summary": "", "error": "boom"},
                     store=store)
    a = failure_profile(store, "worker-bot", "fix-flaky-integration")
    b = failure_profile(store, "worker-bot", "fix-flaky-integration")
    assert a == b  # pure function of the stream — no hidden state
    # The recovery suggestion itself must not inflate the failure count.
    assert a["count"] == 1
    assert a["last_strategy"] == "retry"


# ---------------------------------------------------------------------------
# 3. suggest_recovery — escalation ladder
# ---------------------------------------------------------------------------

def test_first_failure_suggests_retry(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    task = _routed(store)
    _fail(store, task)

    suggestion = suggest_recovery(task, {"status": "failed", "summary": "", "error": "boom"},
                                  store=store)
    assert suggestion is not None
    assert suggestion["strategy"] == "retry"
    assert suggestion["attempt"] == 1
    assert suggestion["bot_id"] == "worker-bot"
    assert suggestion["leaf_id"] == task["leaf_id"]
    assert suggestion["goal"] == task["goal"]

    evts = [e for e in store.get_all(EVENT_RECOVERY)]
    assert len(evts) == 1
    assert evts[0].payload["strategy"] == "retry"
    assert evts[0].correlation_id == task["leaf_id"]


def test_repeated_failure_escalates_to_replan(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    suggestion = None
    for _ in range(2):
        task = _routed(store)
        _fail(store, task)
        suggestion = suggest_recovery(task, {"status": "failed", "summary": "", "error": "boom"},
                                      store=store)
    assert suggestion is not None
    assert suggestion["strategy"] == "replan"
    assert suggestion["attempt"] == 2


def test_reassign_consumes_select_specialists(civ_store):
    """With a better-reputed specialist in the same domain, attempt 2 -> REASSIGN."""
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    create_bot("expert-bot", "testing", event_store=store)
    SocietyManager(store).record_reputation(
        subject_bot="expert-bot", domain="testing", evidence_ref="rep-seed",
        outcome="success", delta_hint=0.1,
    )

    task = _routed(store)
    _fail(store, task)
    suggest_recovery(task, {"status": "failed", "summary": "", "error": "boom"}, store=store)

    task2 = _routed(store)
    _fail(store, task2)
    suggestion = suggest_recovery(task2, {"status": "failed", "summary": "", "error": "boom"},
                                  store=store)
    assert suggestion["strategy"] == "reassign"
    assert suggestion["suggested_bot"] == "expert-bot"


def test_enabled_strategies_filter_falls_back(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    pol = RecoveryPolicy(enabled_strategies=frozenset({RecoveryStrategy.RETRY}))
    task = _routed(store)
    _fail(store, task)
    suggestion = suggest_recovery(task, {"status": "failed", "summary": "", "error": "boom"},
                                  store=store, policy=pol)
    assert suggestion["strategy"] == "retry"

    task2 = _routed(store)
    _fail(store, task2)
    suggestion2 = suggest_recovery(task2, {"status": "failed", "summary": "", "error": "boom"},
                                   store=store, policy=pol)
    # REPLAN is disabled -> ladder falls back to the only enabled strategy.
    assert suggestion2["strategy"] == "retry"


def test_enabled_strategies_none_means_all(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    task = _routed(store)
    _fail(store, task)
    assert suggest_recovery(task, {"status": "failed", "summary": "", "error": "boom"},
                            store=store, policy=RecoveryPolicy(enabled_strategies=None)) is not None


# ---------------------------------------------------------------------------
# 4. Fail-closed ceiling
# ---------------------------------------------------------------------------

def test_halt_at_max_retries(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    suggestion = None
    for _ in range(3):
        task = _routed(store)
        _fail(store, task)
        suggestion = suggest_recovery(task, {"status": "failed", "summary": "", "error": "boom"},
                                      store=store)
    assert suggestion["strategy"] == "halt"
    assert suggestion["reason"] == "max_retries_exceeded"
    assert suggestion["attempt"] == 3

    # Beyond the ceiling it stays halted (fail-closed, never loops).
    task4 = _routed(store)
    _fail(store, task4)
    again = suggest_recovery(task4, {"status": "failed", "summary": "", "error": "boom"},
                             store=store)
    assert again["strategy"] == "halt"


def test_halt_ceiling_is_per_goal_not_global(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    for _ in range(3):
        task = _routed(store, goal="Fix flaky integration test")
        _fail(store, task)
        suggest_recovery(task, {"status": "failed", "summary": "", "error": "boom"}, store=store)

    fresh_goal_task = _routed(store, goal="Write release notes")
    _fail(store, fresh_goal_task)
    suggestion = suggest_recovery(fresh_goal_task, {"status": "failed", "summary": "", "error": "boom"},
                                  store=store)
    assert suggestion["strategy"] == "retry"
    assert suggestion["attempt"] == 1


def test_halt_disabled_keeps_escalating(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    pol = RecoveryPolicy(halt_on_max_retries=False)
    suggestion = None
    for _ in range(3):
        task = _routed(store)
        _fail(store, task)
        suggestion = suggest_recovery(task, {"status": "failed", "summary": "", "error": "boom"},
                                      store=store, policy=pol)
    assert suggestion["strategy"] != "halt"


# ---------------------------------------------------------------------------
# 3b. Revalidation of insufficient DONE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("summary", ["", "   ", "[insufficient] partial work only",
                                     "short", "Short"])
def test_insufficient_done_marks_event_and_neutral_reputation(civ_store, summary):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    task = _routed(store)
    record_task_result(task, {"status": "completed", "summary": summary}, event_store=store)

    completed = [e for e in store.get_all(EVENT_COMPLETED) if e.payload.get("leaf_id") == task["leaf_id"]]
    assert len(completed) == 1
    # Leaf stays 'completed' in the log (no downgrade — documented choice),
    # but the payload is revalidated as insufficient.
    assert completed[0].payload["status"] == "insufficient"

    rep = [e for e in store.get_all("civ.society.reputation-event-recorded")][-1]
    assert rep.payload["reputation"]["delta_hint"] == 0.0
    assert rep.payload["reputation"]["outcome"] == "neutral"

    # suggest_recovery treats it as a light failure.
    suggestion = suggest_recovery(task, {"status": "completed", "summary": summary}, store=store)
    assert suggestion is not None
    assert suggestion["strategy"] == "retry"
    assert suggestion["reason"] == "insufficient_result"
    assert failure_profile(store, "worker-bot", "fix-flaky-integration")["count"] == 1


def test_sufficient_done_not_insufficient(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    task = _routed(store)
    record_task_result(task, {"status": "completed",
                              "summary": "Implemented and tested the full pipeline"},
                       event_store=store)
    completed = [e for e in store.get_all(EVENT_COMPLETED)
                 if e.payload.get("leaf_id") == task["leaf_id"]][-1]
    assert completed.payload["status"] == "completed"
    rep = [e for e in store.get_all("civ.society.reputation-event-recorded")][-1]
    assert rep.payload["reputation"]["delta_hint"] > 0
    # A healthy success never triggers a recovery suggestion.
    assert suggest_recovery(task, {"status": "completed",
                                  "summary": "Implemented and tested the full pipeline"},
                            store=store) is None


def test_short_summary_with_tool_evidence_is_sufficient(civ_store):
    """length<8 alone is not insufficiency — registered tool calls count as evidence."""
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    task = _routed(store)
    record_task_result(task, {"status": "completed", "summary": "Done ok",
                              "tool_trace": [{"tool": "terminal", "status": "ok"}]},
                       event_store=store)
    completed = [e for e in store.get_all(EVENT_COMPLETED)
                 if e.payload.get("leaf_id") == task["leaf_id"]][-1]
    assert completed.payload["status"] == "completed"


# ---------------------------------------------------------------------------
# 5. Rollout flag HAOS_CIV_RECOVERY
# ---------------------------------------------------------------------------

def test_flag_off_is_full_noop(civ_store, monkeypatch):
    """HAOS_CIV_RECOVERY=0: record_task_result/leaf identical to pre-ADR; no suggestion."""
    monkeypatch.setenv("HAOS_CIV_RECOVERY", "0")
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    task = _routed(store)
    record_task_result(task, {"status": "completed", "summary": ""}, event_store=store)

    completed = [e for e in store.get_all(EVENT_COMPLETED)
                 if e.payload.get("leaf_id") == task["leaf_id"]][-1]
    # No revalidation: payload status stays exactly 'completed' as before the ADR.
    assert completed.payload["status"] == "completed"
    rep = [e for e in store.get_all("civ.society.reputation-event-recorded")][-1]
    assert rep.payload["reputation"]["delta_hint"] > 0  # old +0.1 behaviour

    assert suggest_recovery(task, {"status": "completed", "summary": ""}, store=store) is None
    assert store.get_all(EVENT_RECOVERY) == []

    # Failure path also emits no recovery events with the flag off.
    task2 = _routed(store)
    _fail(store, task2)
    assert suggest_recovery(task2, {"status": "failed", "summary": "", "error": "boom"},
                            store=store) is None
    assert store.get_all(EVENT_RECOVERY) == []


def test_flag_on_is_default(civ_store):
    store = civ_store
    create_bot("worker-bot", "testing", event_store=store)
    task = _routed(store)
    _fail(store, task)
    assert suggest_recovery(task, {"status": "failed", "summary": "", "error": "boom"},
                            store=store) is not None


# ---------------------------------------------------------------------------
# Robustness: never crash the delegation path
# ---------------------------------------------------------------------------

def test_suggest_recovery_survives_garbage_inputs(civ_store):
    assert suggest_recovery(None, None, store=civ_store) is None
    assert suggest_recovery({}, {}, store=civ_store) is None
    assert suggest_recovery({"bot_id": "b"}, {"status": "failed"}, store=civ_store) is None
