"""Comprehensive unit tests for autonomous CouncilDebateRunner, budgets, and outbox."""

import tempfile
from pathlib import Path

import pytest

from hermes.platform.bots.identity import (
    BotIdentityBundle,
    CouncilSpec,
)
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.civilization.manager import (
    CivilizationManager,
    ConstitutionRule,
    RULE_TYPE_HARD_DENY,
)
from hermes.platform.council.budget import BudgetExhaustedError, CouncilBudget
from hermes.platform.council.debate_runner import CouncilDebateRunner
from hermes.platform.council.inbox import CommandInbox
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.member_runner import MemberRunner
from hermes.platform.council.outbox import CouncilOutbox
from hermes.platform.council.synthesis import SynthesisBot
from hermes.platform.observability.event_store import EventStore


@pytest.fixture
def temp_env():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = EventStore(root / "events.db")
        id_mgr = IdentityManager(store)
        civ_mgr = CivilizationManager(store)
        council_mgr = CouncilManager(store)
        inbox = CommandInbox(store)
        outbox = CouncilOutbox(store)

        # Register two test bots with active identities
        bundle_a = BotIdentityBundle(
            bot_id="arch-bot",
            soul="I am architect.",
            identity="Architect",
            values="Stability first",
        )
        id_mgr.create_version("arch-bot", bundle_a, activate=True)

        bundle_b = BotIdentityBundle(
            bot_id="sec-bot",
            soul="I am security.",
            identity="Security",
            values="Fail-closed",
        )
        id_mgr.create_version("sec-bot", bundle_b, activate=True)

        # Register test council
        council_spec = CouncilSpec(
            id="arch-sec-council",
            purpose="Architecture & Security Council",
            members=["arch-bot", "sec-bot"],
            decision_mode="consensus_with_dissent",
            budget={"max_rounds": 2, "max_tokens": 10000, "max_cost_usd": 1.0},
        )
        council_mgr.register(council_spec)

        member_runner = MemberRunner(identity_provider=id_mgr)
        runner = CouncilDebateRunner(
            council_manager=council_mgr,
            member_runner=member_runner,
            synthesis_bot=SynthesisBot(),
            civ_manager=civ_mgr,
            inbox=inbox,
            outbox=outbox,
        )

        yield {
            "root": root,
            "store": store,
            "civ_mgr": civ_mgr,
            "council_mgr": council_mgr,
            "runner": runner,
            "inbox": inbox,
            "outbox": outbox,
        }


def test_debate_runner_full_cycle(temp_env):
    runner = temp_env["runner"]
    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Standardize distributed caching policy",
        command_id="cmd-cache-001",
    )

    assert res["status"] == "completed"
    assert res["decision_id"].startswith("dec-")
    assert len(res["participants"]) == 2
    assert "arch-bot" in res["participants"]
    assert "sec-bot" in res["participants"]
    assert res["budget_usage"]["rounds_used"] >= 0
    assert res["budget_usage"]["tokens_used"] > 0

    # Test idempotency: re-running with same command_id returns cached result
    res_duplicate = runner.deliberate(
        council_id="arch-sec-council",
        objective="Standardize distributed caching policy",
        command_id="cmd-cache-001",
    )
    assert res_duplicate["decision_id"] == res["decision_id"]


def test_debate_runner_multi_turn_with_dissent(temp_env):
    runner = temp_env["runner"]
    # Objective contains 'urgent', triggering mock dissent in sec-bot.
    # C1: a BLOCKED verdict must NOT silently complete or schedule actions;
    # the session is left pending human approval.
    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Urgent database migration to new cluster",
        command_id="cmd-urgent-001",
        options={"max_rounds": 2},
    )

    assert res["status"] == "pending_human_approval"
    assert "sec-bot" in res["dissent"]
    assert "rushed timeline" in res["dissent"]["sec-bot"]
    assert "dissent" in res["decision"].lower()
    # C1: nothing was scheduled into the outbox
    assert temp_env["outbox"].get_pending() == []
    # C1: session recorded as pending (paused), not silently completed
    sess = temp_env["council_mgr"].get_session(res["session_id"])
    assert sess.phase == "paused"
    assert "human_approval" in (sess.metadata.get("paused_reason") or "")


def test_debate_runner_hard_budget_enforcement(temp_env):
    runner = temp_env["runner"]
    with pytest.raises(BudgetExhaustedError) as exc_info:
        runner.deliberate(
            council_id="arch-sec-council",
            objective="Exceed token budget test",
            options={"max_tokens": 100},  # Impossibly tight token budget
        )
    assert "max_tokens" in str(exc_info.value)


def test_debate_runner_policy_hard_deny(temp_env):
    civ_mgr = temp_env["civ_mgr"]
    runner = temp_env["runner"]

    # Enact a constitution with hard deny on execute_objective for this council
    rule = ConstitutionRule(
        id="rule-no-destroy",
        name="Block Destructive Action",
        description="Hard deny",
        rule_type=RULE_TYPE_HARD_DENY,
        target_action="execute_objective",
    )
    civ_mgr.enact_constitution(version=1, title="Test Constitution", rules=[rule], approved_by="admin")

    with pytest.raises(PermissionError) as exc_info:
        runner.deliberate(
            council_id="arch-sec-council",
            objective="Standardize caching policy",
            command_id="cmd-policy-deny-001",
        )
    assert "Constitutional Hard Deny" in str(exc_info.value)


def test_council_outbox_scheduling_and_dispatch(temp_env):
    outbox = temp_env["outbox"]
    runner = temp_env["runner"]

    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Deploy cache infrastructure",
        command_id="cmd-outbox-001",
        approved=True,
    )
    assert res["action_gate"] == "approved"

    pending = outbox.get_pending()
    assert len(pending) >= 1
    assert pending[0].action_type == "execute_objective"

    # Register handler and dispatch
    dispatched = outbox.dispatch_pending(
        worker_id="worker-test-1",
        handlers={"execute_objective": lambda payload: {"deployed": True, "target": payload.get("resource")}},
    )
    assert len(dispatched) == 1
    assert dispatched[0]["status"] == "completed"
    assert dispatched[0]["result"]["deployed"] is True


# ---------------------------------------------------------------------------
# Regression tests for audited bugs (C1, C5, C2, A1, A2, A3, M3)
# ---------------------------------------------------------------------------

def _make_paused_session(council_mgr, command_id, objective="Standardize distributed caching policy", budget=None):
    """Create a session with round-0 positions submitted, then pause it (resume fixture)."""
    session = council_mgr.start_session(
        council_id="arch-sec-council",
        objective=objective,
        command_id=command_id,
        budget=budget or CouncilBudget(max_rounds=2, max_tokens=10000, max_cost_usd=1.0).to_dict(),
    )
    council_mgr.submit_position(session.session_id, "arch-bot", "[arch-bot] ok", cost_usd=0.0015, tokens=150)
    council_mgr.submit_position(session.session_id, "sec-bot", "[sec-bot] ok", cost_usd=0.0015, tokens=150)
    council_mgr.pause_session(session.session_id, "operator pause")
    return council_mgr.get_session(session.session_id)


def test_c1_needs_human_approval_blocks_scheduling(temp_env):
    """C1: needs_human_approval=True (even with an APPROVED verdict) must not schedule actions."""
    runner = temp_env["runner"]
    # No 'urgent' -> no dissent, but 'production' in objective forces needs_human_approval.
    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Roll new auth service to production",
        command_id="cmd-prod-001",
    )
    assert res["status"] == "pending_human_approval"
    assert temp_env["outbox"].get_pending() == []
    sess = temp_env["council_mgr"].get_session(res["session_id"])
    assert sess.phase == "paused"


def test_c1_approved_flag_clears_needs_human_gate(temp_env):
    """C1: explicit operator approval satisfies the needs_human_approval gate (verdict not BLOCKED)."""
    runner = temp_env["runner"]
    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Roll new auth service to production",
        command_id="cmd-prod-002",
        approved=True,
    )
    assert res["status"] == "completed"
    assert len(temp_env["outbox"].get_pending()) >= 1


def test_c1_blocked_session_can_be_reapproved_on_resume(temp_env):
    """C1: a session parked pending_human_approval must be re-drivable with approved=True
    (same command_id resumes the paused session instead of returning the cached pending result)."""
    runner = temp_env["runner"]
    first = runner.deliberate(
        council_id="arch-sec-council",
        objective="Roll new auth service to production",
        command_id="cmd-prod-003",
    )
    assert first["status"] == "pending_human_approval"
    assert temp_env["outbox"].get_pending() == []

    second = runner.deliberate(
        council_id="arch-sec-council",
        objective="Roll new auth service to production",
        command_id="cmd-prod-003",
        approved=True,
    )
    assert second["status"] == "completed"
    assert second["session_id"] == first["session_id"]
    assert second["decision_id"].startswith("dec-")
    assert len(temp_env["outbox"].get_pending()) >= 1


def test_c5_resume_paused_session_does_not_crash(temp_env):
    """C5: resuming a 'paused' session must re-run synthesis instead of UnboundLocalError."""
    council_mgr = temp_env["council_mgr"]
    runner = temp_env["runner"]
    session = _make_paused_session(council_mgr, command_id="cmd-resume-001")
    assert session.phase == "paused"

    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Standardize distributed caching policy",
        command_id="cmd-resume-001",
    )
    assert res["status"] in ("completed", "pending_human_approval")
    assert res["decision_id"].startswith("dec-")
    assert res["session_id"] == session.session_id


def test_a1_budget_restored_on_resume(temp_env):
    """A1: resume must restore budget config/counters from the session, not rebuild from zero."""
    import time as _time
    council_mgr = temp_env["council_mgr"]
    runner = temp_env["runner"]
    original = CouncilBudget(max_rounds=2, max_tokens=10000, max_cost_usd=1.0).to_dict()
    original["started_at"] = _time.time() - 10.0  # clock started before the pause
    session = _make_paused_session(council_mgr, command_id="cmd-budget-001", budget=original)
    # 300 tokens / 0.003 USD already spent on round-0 positions.
    assert session.tokens_used == 300

    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Standardize distributed caching policy",
        command_id="cmd-budget-001",
    )
    usage = res["budget_usage"]
    assert usage["started_at"] == original["started_at"]  # timeout clock preserved
    assert usage["tokens_used"] >= 300  # prior usage not zeroed
    assert usage["cost_usd_used"] >= 0.003


def test_a2_single_accounting_of_member_cost(temp_env):
    """A2: member cost/tokens must be charged once (submit_position), not double via record_debate_turn."""
    council_mgr = temp_env["council_mgr"]
    runner = temp_env["runner"]
    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Standardize distributed caching policy",
        command_id="cmd-acct-001",
    )
    sess = council_mgr.get_session(res["session_id"])
    # Two members x 150 tokens / 0.0015 USD. Pre-fix replay summed 600 / 0.003.
    assert sess.tokens_used == 300
    assert abs(sess.cost_usd - 0.003) < 1e-9


def test_a3_retracted_dissent_stops_debate(temp_env):
    """A3: a bot that retracts its dissent (dissent=None) must not keep the debate loop burning rounds."""
    store = temp_env["store"]
    council_mgr = temp_env["council_mgr"]

    calls = {"count": 0}

    def retracting_executor(prompt, context):
        calls["count"] += 1
        bot_id = context.get("bot_id", "bot")
        rnd = context.get("round", 0)
        dissent = "Concerns on rushed timeline." if (bot_id == "sec-bot" and rnd == 0) else None
        return {
            "position": f"[{bot_id}] round {rnd}",
            "confidence": 0.8,
            "dissent": dissent,
            "tokens_used": 100,
            "cost_usd": 0.001,
        }

    member_runner = MemberRunner(
        identity_provider=IdentityManager(store), executor=retracting_executor
    )
    runner = CouncilDebateRunner(
        council_manager=council_mgr,
        member_runner=member_runner,
        synthesis_bot=SynthesisBot(),
        civ_manager=temp_env["civ_mgr"],
        inbox=CommandInbox(store),
        outbox=CouncilOutbox(store),
    )
    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Urgent database migration to new cluster",
        command_id="cmd-retract-001",
        options={"max_rounds": 5},
    )
    # Round 0 (2 calls) + exactly one debate round (2 calls); sec-bot retracted -> loop stops.
    assert res["budget_usage"]["rounds_used"] == 1
    assert calls["count"] == 4


def test_m3_max_rounds_one_runs_one_debate_round(temp_env):
    """M3: max_rounds=1 must allow one debate round (off-by-one gave zero)."""
    runner = temp_env["runner"]
    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Urgent database migration to new cluster",
        command_id="cmd-rounds-001",
        options={"max_rounds": 1},
    )
    assert res["budget_usage"]["rounds_used"] == 1


def test_c2_majority_mode_real_vote_tally():
    """C2: 'majority' must tally votes on positions, not reuse the dissent-count consensus rule."""
    bot = SynthesisBot()
    # 2 of 3 vote the same -> majority approval
    res = bot.synthesize(
        council_id="c", session_id="s", objective="obj",
        positions={"a": {"vote": "yes"}, "b": {"vote": "yes"}, "c": {"vote": "no"}},
        dissent_map={}, decision_mode="majority",
    )
    assert "APPROVED by majority" in res.decision
    assert "BLOCKED" not in res.decision

    # 1-1 tie -> no majority -> blocked
    tie = bot.synthesize(
        council_id="c", session_id="s", objective="obj",
        positions={"a": {"vote": "yes"}, "b": {"vote": "no"}},
        dissent_map={}, decision_mode="majority",
    )
    assert "BLOCKED" in tie.decision


def test_c2_single_synthesizer_mode_decides_alone():
    """C2: 'single_synthesizer' decides regardless of dissent proportion."""
    bot = SynthesisBot()
    res = bot.synthesize(
        council_id="c", session_id="s", objective="obj",
        positions={"a": {"vote": "yes"}, "b": {"vote": "no"}, "c": {"vote": "no"}},
        dissent_map={"b": "no", "c": "no"}, decision_mode="single_synthesizer",
    )
    assert res.decision.startswith("APPROVED by single synthesizer")
    # Consensus rule would have BLOCKED here (2 dissents >= 3/2); synthesizer mode must not.
    consensus = bot.synthesize(
        council_id="c", session_id="s", objective="obj",
        positions={"a": {"vote": "yes"}, "b": {"vote": "no"}, "c": {"vote": "no"}},
        dissent_map={"b": "no", "c": "no"}, decision_mode="consensus_with_dissent",
    )
    assert "BLOCKED" in consensus.decision
