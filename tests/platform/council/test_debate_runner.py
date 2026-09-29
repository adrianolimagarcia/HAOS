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
from hermes.platform.council.budget import BudgetExhaustedError
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
    # Objective contains 'urgent', triggering mock dissent in sec-bot
    res = runner.deliberate(
        council_id="arch-sec-council",
        objective="Urgent database migration to new cluster",
        command_id="cmd-urgent-001",
        options={"max_rounds": 2},
    )

    assert res["status"] == "completed"
    assert "sec-bot" in res["dissent"]
    assert "rushed timeline" in res["dissent"]["sec-bot"]
    assert "dissent" in res["decision"].lower()


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
