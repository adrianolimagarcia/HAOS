"""Tests for REAL council deliberation on high-risk promotions (human route).

Covers the Council-fachada follow-up (operator decision): RISK_HIGH /
RISK_IDENTITY_CRITICAL promotions through
``deliberate_and_promote_proposal`` are decided by an actual deliberation of
an injected LeafExecutor, fail-closed:

- majority APPROVE  -> promotion proceeds, DecisionRecord stamped
  deliberation="real", bundle metadata carries deliberation="real" +
  ratification="council-deliberated" + cost observability.
- majority REJECT / tie -> PermissionError with decision_id + votes; proposal
  stays in review (never approved/canary/promoted); no new identity version.
- executor crash / invalid vote / budget blow-up -> PermissionError
  "refusing to fall back to facade"; no version created.
- RISK_LOW -> zero regression: facade path untouched (no council session even).
- HAOS_CIV_REAL_DELIBERATION=0 -> high risk uses the facade with an INFO log.

Determinism: no live LLM is ever called here; the model path is exercised
only through injected callables (member_executor / llm_call).
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

import pytest

from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.civilization.delegation import (
    create_bot,
    deliberate_and_promote_proposal,
)
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.promotion_executor import (
    PROMOTION_COUNCIL_ID,
    build_model_member_executor,
)
from hermes.platform.council.spec import CouncilSpec
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    RISK_HIGH,
    RISK_IDENTITY_CRITICAL,
    RISK_LOW,
    STATUS_DRAFT,
    STATUS_PROMOTED,
)
from hermes.platform.evolution.promotion_holdout_gate import HoldoutVerdict
from hermes.platform.observability.event_store import EventStore

APPROVED_EVAL = {"accepted": True, "reason": "held-out suite passed", "evidence": ["ok"]}


@pytest.fixture
def store_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = EventStore(db_path=str(Path(tmpdir) / "events.db"))
        yield {
            "root": Path(tmpdir),
            "store": store,
            "id_mgr": IdentityManager(store),
            "evo_mgr": BotEvolutionManager(store),
            "council_mgr": CouncilManager(store),
        }


class _AcceptingGate:
    def evaluate(self, baseline_root, candidate_root):
        return HoldoutVerdict(accepted=True, reason="injected pass")


def _make_high_proposal(env, risk=RISK_HIGH):
    """Subject bot + two peer bots (implicit promotion-council members)."""
    store = env["store"]
    create_bot("architect-bot", "architecture", description="Architect", event_store=store)
    create_bot("security-bot", "security", description="Security", event_store=store)
    create_bot("data-bot", "data", description="Data", event_store=store)
    active = env["id_mgr"].get_active_version("architect-bot")
    evo = env["evo_mgr"]
    evo.record_experience(
        bot_id="architect-bot", event_type="mission_completed",
        domain="architecture", summary="learned", success=True,
    )
    return evo.create_proposal(
        bot_id="architect-bot",
        base_version_hash=active.bundle_hash,
        risk_class=risk,
        rationale="adopt stricter evidence policy",
        proposed_values_patch="Evidence before confidence, always.",
    )


class _FakeExecutor:
    """Deterministic LeafExecutor: scripted ballots per bot_id, call log kept."""

    def __init__(self, votes_by_bot: dict, *, tokens: int = 100, cost: float = 0.001):
        self.votes_by_bot = votes_by_bot
        self.tokens = tokens
        self.cost = cost
        self.calls: list = []

    def __call__(self, prompt, context):
        bot_id = context["bot_id"]
        self.calls.append({"bot_id": bot_id, "prompt": prompt, "context": context})
        vote = self.votes_by_bot.get(bot_id, "APPROVE")
        return {
            "position": f"{bot_id} says {vote} from its frozen identity.",
            "vote": vote,
            "confidence": 0.9,
            "dissent": "minor concern" if vote == "REJECT" else None,
            "tokens_used": self.tokens,
            "cost_usd": self.cost,
            "model": "fake-test-model",
        }


def _fake_executor(votes_by_bot: dict, *, tokens: int = 100, cost: float = 0.001):
    return _FakeExecutor(votes_by_bot, tokens=tokens, cost=cost)


def _promote(env, prop, executor, **kw):
    return deliberate_and_promote_proposal(
        prop.id,
        approver="adriano",
        evaluation=APPROVED_EVAL,
        holdout_gate=_AcceptingGate(),
        member_executor=executor,
        event_store=env["store"],
        **kw,
    )


# ---------------------------------------------------------------------------
# (a) high-risk + approving council -> promotes with deliberation="real"
# ---------------------------------------------------------------------------

def test_high_risk_real_deliberation_approves_and_promotes(store_env):
    prop = _make_high_proposal(store_env)
    executor = _fake_executor({"security-bot": "APPROVE", "data-bot": "APPROVE"})
    res = _promote(store_env, prop, executor)

    assert res["deliberation"] == "real"
    assert res["council_decision_id"] and res["council_session_id"]
    assert res["new_version_number"] == 2

    decision = store_env["council_mgr"].get_decision(res["council_decision_id"])
    assert decision is not None
    assert decision.deliberation == "real"
    assert decision.metadata.get("ratification") != "automated"
    # votes frozen into positions
    assert set(decision.positions) == {"security-bot", "data-bot"}
    for pos in decision.positions.values():
        assert pos["vote"] == "APPROVE"
        assert pos["ratification"] == "deliberated"

    meta = store_env["id_mgr"].get_active_version("architect-bot").bundle.metadata
    assert meta["deliberation"] == "real"
    assert meta["ratification"] == "council-deliberated"
    assert meta["deliberation_cost"]["tokens_used"] == 200
    assert meta["deliberation_cost"]["cost_usd"] == pytest.approx(0.002)

    # subject bot never votes on its own promotion
    assert all(c["context"]["bot_id"] != "architect-bot" for c in executor.calls)
    # each member saw the other's position only after submitting (sequential)
    assert len(executor.calls) == 2

    # observability event carries tokens/cost
    delib_events = [e for e in store_env["store"].get_all()
                    if e.name == "civ.promotion.real_deliberation"]
    assert len(delib_events) == 1
    payload = delib_events[0].payload
    assert payload["approved"] is True
    assert payload["tokens_used"] == 200
    assert payload["votes"] == {"security-bot": "APPROVE", "data-bot": "APPROVE"}

    fresh = {p.id: p for p in store_env["evo_mgr"].get_proposals()}
    assert fresh[prop.id].status == STATUS_PROMOTED


def test_identity_critical_uses_real_deliberation_too(store_env):
    prop = _make_high_proposal(store_env, risk=RISK_IDENTITY_CRITICAL)
    res = _promote(store_env, prop, _fake_executor({}))
    assert res["deliberation"] == "real"
    assert store_env["id_mgr"].get_active_version("architect-bot").version == 2


def test_member_prompts_carry_identity_and_proposal(store_env):
    prop = _make_high_proposal(store_env)
    executor = _fake_executor({})
    _promote(store_env, prop, executor)
    call = executor.calls[0]
    # system prompt embeds the member's frozen identity bundle
    assert "SOUL:" in call["context"]["system_prompt"]
    assert call["context"]["system_prompt"].startswith("You are security-bot")
    # user prompt carries the proposal under deliberation
    assert prop.id in call["prompt"]
    assert "risk high" in call["prompt"]
    assert "Respond with ONLY a JSON object" in call["prompt"]


def test_named_council_is_honored(store_env):
    store_env["council_mgr"].register(CouncilSpec(
        id="oversight", purpose="Evolution oversight",
        members=["security-bot", "data-bot"], decision_mode="majority",
    ))
    prop = _make_high_proposal(store_env)
    res = _promote(store_env, prop, _fake_executor({}), council_id="oversight")
    assert res["deliberation"] == "real"
    assert res["council_decision_id"].startswith("dec-oversight-")


def test_missing_named_council_fails_closed(store_env):
    prop = _make_high_proposal(store_env)
    with pytest.raises(PermissionError, match="refusing to fall back to facade"):
        _promote(store_env, prop, _fake_executor({}), council_id="ghost-council")
    assert store_env["id_mgr"].get_active_version("architect-bot").version == 1


# ---------------------------------------------------------------------------
# (b) majority REJECT -> refused, state preserved, no version
# ---------------------------------------------------------------------------

def test_majority_reject_refuses_promotion_and_keeps_state(store_env):
    prop = _make_high_proposal(store_env)
    executor = _fake_executor({"security-bot": "APPROVE", "data-bot": "REJECT"})
    with pytest.raises(PermissionError) as exc_info:
        _promote(store_env, prop, executor)
    msg = str(exc_info.value)
    assert "real council deliberation" in msg
    assert "decision_id=" in msg and "votes=" in msg

    # no new identity version
    assert store_env["id_mgr"].get_active_version("architect-bot").version == 1
    # deliberation happens BEFORE the FSM chain: refused proposal stays in
    # draft — never approved/canary/promoted
    fresh = {p.id: p for p in store_env["evo_mgr"].get_proposals()}
    assert fresh[prop.id].status == STATUS_DRAFT
    # the REJECT decision is still recorded honestly
    decisions = store_env["council_mgr"].list_decisions(PROMOTION_COUNCIL_ID)
    assert decisions and decisions[0].deliberation == "real"
    assert "REJECTED / DIVIDED" in decisions[0].decision


def test_tie_votes_blocks_promotion(store_env):
    # 4 members: 2 APPROVE / 2 REJECT -> approve*2 == n, no strict majority
    store_env["council_mgr"].register(CouncilSpec(
        id="even-council", purpose="Even split test",
        members=["security-bot", "data-bot", "qa-bot", "ops-bot"],
        decision_mode="majority",
    ))
    store = store_env["store"]
    create_bot("qa-bot", "qa", event_store=store)
    create_bot("ops-bot", "ops", event_store=store)
    prop = _make_high_proposal(store_env)
    executor = _fake_executor({
        "security-bot": "APPROVE", "data-bot": "APPROVE",
        "qa-bot": "REJECT", "ops-bot": "REJECT",
    })
    with pytest.raises(PermissionError, match="real council deliberation"):
        _promote(store_env, prop, executor, council_id="even-council")
    assert store_env["id_mgr"].get_active_version("architect-bot").version == 1


# ---------------------------------------------------------------------------
# (c) executor failure -> fail-closed, never facade
# ---------------------------------------------------------------------------

def test_executor_crash_fails_closed_no_facade(store_env):
    prop = _make_high_proposal(store_env)

    def boom(prompt, context):
        raise RuntimeError("model is down")

    with pytest.raises(PermissionError) as exc_info:
        _promote(store_env, prop, boom)
    assert "real deliberation unavailable; refusing to fall back to facade" in str(exc_info.value)
    assert store_env["id_mgr"].get_active_version("architect-bot").version == 1
    fresh = {p.id: p for p in store_env["evo_mgr"].get_proposals()}
    assert fresh[prop.id].status == STATUS_DRAFT
    # session marked failed, no decision recorded
    sessions = store_env["council_mgr"].list_sessions(PROMOTION_COUNCIL_ID)
    assert sessions and sessions[0].phase == "failed"
    assert store_env["council_mgr"].list_decisions(PROMOTION_COUNCIL_ID) == []


def test_invalid_vote_fails_closed(store_env):
    prop = _make_high_proposal(store_env)

    def maybe(prompt, context):
        if context["bot_id"] == "data-bot":
            return {"position": "confused", "vote": "MAYBE", "confidence": 0.5}
        return {"position": "ok", "vote": "APPROVE", "confidence": 0.8}

    with pytest.raises(PermissionError, match="refusing to fall back to facade"):
        _promote(store_env, prop, maybe)
    assert store_env["id_mgr"].get_active_version("architect-bot").version == 1


def test_budget_exhaustion_fails_closed(store_env):
    prop = _make_high_proposal(store_env)
    # 5000 tokens/member x 2 members > PROMOTION_MAX_TOKENS (8000)
    executor = _fake_executor({}, tokens=5000)
    with pytest.raises(PermissionError, match="refusing to fall back to facade"):
        _promote(store_env, prop, executor)
    assert store_env["id_mgr"].get_active_version("architect-bot").version == 1


def test_insufficient_members_fails_closed(store_env):
    """Only one active non-subject bot -> cannot form a real council; the
    promotion is refused rather than degraded to the facade."""
    store = store_env["store"]
    create_bot("architect-bot", "architecture", event_store=store)
    create_bot("security-bot", "security", event_store=store)
    active = store_env["id_mgr"].get_active_version("architect-bot")
    evo = store_env["evo_mgr"]
    evo.record_experience(bot_id="architect-bot", event_type="mission_completed",
                          domain="architecture", summary="l", success=True)
    prop = evo.create_proposal(
        bot_id="architect-bot", base_version_hash=active.bundle_hash,
        risk_class=RISK_HIGH, rationale="test",
        proposed_values_patch="patch",
    )
    with pytest.raises(PermissionError, match="refusing to fall back to facade"):
        _promote(store_env, prop, _fake_executor({}))


# ---------------------------------------------------------------------------
# (d) low-risk -> facade unchanged (zero regression)
# ---------------------------------------------------------------------------

def test_low_risk_uses_facade_and_never_real(store_env):
    prop = _make_high_proposal(store_env, risk=RISK_LOW)
    executor = _fake_executor({})
    res = _promote(store_env, prop, executor)
    assert res["deliberation"] == "none"  # no council_id given -> direct human route
    assert executor.calls == []           # real executor never invoked
    assert store_env["council_mgr"].list_sessions(PROMOTION_COUNCIL_ID) == []
    assert store_env["id_mgr"].get_active_version("architect-bot").version == 2


def test_low_risk_with_council_stays_facade(store_env):
    store_env["council_mgr"].register(CouncilSpec(
        id="oversight", purpose="Evolution oversight",
        members=["security-bot", "data-bot"],
    ))
    prop = _make_high_proposal(store_env, risk=RISK_LOW)
    res = _promote(store_env, prop, _fake_executor({}), council_id="oversight")
    assert res["deliberation"] == "facade"
    decision = store_env["council_mgr"].get_decision(res["council_decision_id"])
    assert decision.deliberation == "facade"
    assert decision.metadata.get("ratification") == "automated"


# ---------------------------------------------------------------------------
# (e) HAOS_CIV_REAL_DELIBERATION=0 -> facade on high risk with INFO log
# ---------------------------------------------------------------------------

def test_gate_off_high_risk_uses_facade_with_info_log(store_env, monkeypatch, caplog):
    monkeypatch.setenv("HAOS_CIV_REAL_DELIBERATION", "0")
    store_env["council_mgr"].register(CouncilSpec(
        id="oversight", purpose="Evolution oversight",
        members=["security-bot", "data-bot"],
    ))
    prop = _make_high_proposal(store_env)
    executor = _fake_executor({})
    with caplog.at_level(logging.INFO, logger="hermes.platform.civilization.delegation"):
        res = _promote(store_env, prop, executor, council_id="oversight")
    assert res["deliberation"] == "facade"
    assert executor.calls == []
    assert any("HAOS_CIV_REAL_DELIBERATION disabled" in r.getMessage()
               for r in caplog.records)
    decision = store_env["council_mgr"].get_decision(res["council_decision_id"])
    assert decision.deliberation == "facade"


# ---------------------------------------------------------------------------
# Real-LLM executor: parsing/coercion exercised with a fake llm_call only
# ---------------------------------------------------------------------------

class _Msg:
    def __init__(self, content):
        self.message = {"content": content}


class _Resp:
    def __init__(self, content, usage=None, model="test-model"):
        self.choices = [_Msg(content)]
        self.usage = usage
        self.model = model


def test_build_model_member_executor_parses_json_verdict():
    seen = {}

    def fake_llm(task=None, messages=None, **kw):
        seen["task"] = task
        seen["system"] = messages[0]["content"]
        return _Resp(
            "```json\n" + json.dumps({
                "vote": "APPROVE", "position": "Grounded.", "confidence": 0.8,
                "dissent": None,
            }) + "\n```",
            usage=type("U", (), {"total_tokens": 123, "cost_usd": 0.004})(),
        )

    executor = build_model_member_executor(llm_call=fake_llm)
    out = executor("decide", {"bot_id": "x", "system_prompt": "You are x."})
    assert out["vote"] == "APPROVE"
    assert out["position"] == "Grounded."
    assert out["tokens_used"] == 123
    assert out["cost_usd"] == 0.004
    assert out["model"] == "test-model"
    assert seen["task"] == "council_deliberation"
    assert seen["system"] == "You are x."


def test_build_model_member_executor_rejects_unparseable():
    executor = build_model_member_executor(
        llm_call=lambda **kw: _Resp("I think we should be careful here."),
    )
    with pytest.raises(ValueError, match="unparseable verdict"):
        executor("decide", {"bot_id": "x"})
