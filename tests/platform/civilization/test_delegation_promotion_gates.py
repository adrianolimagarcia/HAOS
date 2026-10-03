"""Tests for the hardened human promotion route in civilization.delegation.

Covers audit findings:
- B1: deliberate_and_promote_proposal requires a named human approver and a
  fail-closed HoldoutPromotionGate verdict for high/identity-critical risk or
  missing evaluation evidence.
- M5: identity bundle fields are capped (no unbounded append growth); explicit
  replacement via the [[REPLACE]] marker is the only way past the cap.
- B1-low: rollback_bot_identity marks the originating proposal as rolled_back.
- Council-fachada: automated council ratification is recorded as metadata, not
  presented as real deliberation.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.civilization.delegation import (
    _REPLACE_MARKER,
    create_bot,
    deliberate_and_promote_proposal,
    rollback_bot_identity,
)
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.spec import CouncilSpec
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    RISK_HIGH,
    RISK_IDENTITY_CRITICAL,
    RISK_LOW,
    STATUS_PROMOTED,
    STATUS_ROLLED_BACK,
)
from hermes.platform.evolution.promotion_holdout_gate import HoldoutVerdict
from hermes.platform.observability.event_store import EventStore

APPROVED_EVAL = {"accepted": True, "reason": "held-out suite passed", "evidence": ["ok"]}


@pytest.fixture(autouse=True)
def _facade_mode(monkeypatch):
    """This suite pins the HoldoutPromotionGate/FSM contract, not deliberation
    provenance. High-risk real deliberation (HAOS_CIV_REAL_DELIBERATION,
    default ON) would require a council + executor here; pin the declared
    facade so these gate tests stay focused and deterministic. Real
    deliberation is covered in test_delegation_real_deliberation.py."""
    monkeypatch.setenv("HAOS_CIV_REAL_DELIBERATION", "0")


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


def _make_proposal(env, risk=RISK_LOW, values_patch="Learned lesson: always verify."):
    store = env["store"]
    create_bot("architect-bot", "architecture", description="Architect", event_store=store)
    active = env["id_mgr"].get_active_version("architect-bot")
    evo = env["evo_mgr"]
    evo.record_experience(
        bot_id="architect-bot", event_type="mission_completed",
        domain="architecture", summary="learned", success=True,
    )
    prop = evo.create_proposal(
        bot_id="architect-bot",
        base_version_hash=active.bundle_hash,
        risk_class=risk,
        rationale="test",
        proposed_values_patch=values_patch,
    )
    return prop


class _FakeGate:
    """Injectable gate stand-in: returns a fixed verdict without measuring."""

    def __init__(self, accepted: bool, reason: str = "injected"):
        self.accepted = accepted
        self.reason = reason
        self.calls = []

    def evaluate(self, baseline_root, candidate_root):
        self.calls.append((baseline_root, candidate_root))
        return HoldoutVerdict(accepted=self.accepted, reason=self.reason)


# ---------------------------------------------------------------------------
# B1 — named human approver
# ---------------------------------------------------------------------------

def test_b1_missing_approver_is_refused(store_env):
    prop = _make_proposal(store_env)
    with pytest.raises(PermissionError, match="named human approver"):
        deliberate_and_promote_proposal(
            prop.id, evaluation=APPROVED_EVAL, event_store=store_env["store"],
        )


def test_b1_blank_approver_is_refused(store_env):
    prop = _make_proposal(store_env)
    with pytest.raises(PermissionError, match="named human approver"):
        deliberate_and_promote_proposal(
            prop.id, approver="   ", evaluation=APPROVED_EVAL,
            event_store=store_env["store"],
        )


def test_b1_refusal_leaves_no_new_version(store_env):
    prop = _make_proposal(store_env)
    with pytest.raises(PermissionError):
        deliberate_and_promote_proposal(prop.id, event_store=store_env["store"])
    assert store_env["id_mgr"].get_active_version("architect-bot").version == 1
    # proposal must NOT be marked promoted by a refused call
    fresh = {p.id: p for p in store_env["evo_mgr"].get_proposals()}
    assert fresh[prop.id].status != STATUS_PROMOTED


def test_b1_low_risk_without_evaluation_hits_fail_closed_gate(store_env):
    """No evaluation evidence ⇒ real HoldoutPromotionGate runs; with no trees
    it must refuse fail-closed (never promote on unmeasured evidence)."""
    prop = _make_proposal(store_env, risk=RISK_LOW)
    with pytest.raises(PermissionError, match="HoldoutPromotionGate"):
        deliberate_and_promote_proposal(
            prop.id, approver="operator-adriano", event_store=store_env["store"],
        )


def test_b1_high_risk_refused_even_with_accepted_evaluation_dict(store_env):
    """High risk always goes through the live gate; a self-declared dict is
    not enough. Default gate has no trees ⇒ fail-closed refusal."""
    prop = _make_proposal(store_env, risk=RISK_HIGH)
    with pytest.raises(PermissionError, match="HoldoutPromotionGate"):
        deliberate_and_promote_proposal(
            prop.id, approver="adriano", evaluation=APPROVED_EVAL,
            event_store=store_env["store"],
        )


def test_b1_identity_critical_refused_by_rejecting_gate(store_env):
    prop = _make_proposal(store_env, risk=RISK_IDENTITY_CRITICAL)
    with pytest.raises(PermissionError, match="HoldoutPromotionGate"):
        deliberate_and_promote_proposal(
            prop.id, approver="adriano", evaluation=APPROVED_EVAL,
            holdout_gate=_FakeGate(False, reason="regression on held-out"),
            baseline_root=store_env["root"], candidate_root=store_env["root"],
            event_store=store_env["store"],
        )


def test_b1_high_risk_promotes_with_accepting_gate(store_env):
    prop = _make_proposal(store_env, risk=RISK_HIGH)
    gate = _FakeGate(True, reason="no regression held-in/held-out")
    res = deliberate_and_promote_proposal(
        prop.id, approver="adriano", evaluation=APPROVED_EVAL,
        holdout_gate=gate,
        baseline_root=store_env["root"], candidate_root=store_env["root"],
        event_store=store_env["store"],
    )
    assert res["new_version_number"] == 2
    assert gate.calls == [(store_env["root"], store_env["root"])]
    meta = store_env["id_mgr"].get_active_version("architect-bot").bundle.metadata
    assert meta["approver"] == "adriano"
    assert meta["promotion_gate"]["accepted"] is True


def test_b1_rejected_evaluation_dict_refused(store_env):
    prop = _make_proposal(store_env, risk=RISK_LOW)
    with pytest.raises(PermissionError, match="evaluation not accepted"):
        deliberate_and_promote_proposal(
            prop.id, approver="adriano",
            evaluation={"accepted": False, "reason": "score dropped"},
            event_store=store_env["store"],
        )


# ---------------------------------------------------------------------------
# M5 — bundle growth cap + explicit replacement
# ---------------------------------------------------------------------------

def test_m5_append_over_cap_raises_value_error(store_env):
    huge = "x" * 9000
    prop = _make_proposal(store_env, values_patch=huge)
    with pytest.raises(ValueError, match="would exceed") as exc_info:
        deliberate_and_promote_proposal(
            prop.id, approver="adriano", evaluation=APPROVED_EVAL,
            event_store=store_env["store"],
        )
    # the error must teach the escape hatch: explicit replacement marker
    assert _REPLACE_MARKER in str(exc_info.value)
    # fail-fast: nothing mutated
    assert store_env["id_mgr"].get_active_version("architect-bot").version == 1


def test_m5_replace_marker_sub_instead_of_appends(store_env):
    huge = "x" * 9000
    prop = _make_proposal(store_env, values_patch=f"{_REPLACE_MARKER}\n{huge[:200]}")
    res = deliberate_and_promote_proposal(
        prop.id, approver="adriano", evaluation=APPROVED_EVAL,
        event_store=store_env["store"],
    )
    assert res["new_version_number"] == 2
    values = store_env["id_mgr"].get_active_version("architect-bot").bundle.values
    assert values == huge[:200]  # old content replaced, marker stripped


def test_m5_replace_marker_still_capped(store_env):
    prop = _make_proposal(store_env, values_patch=_REPLACE_MARKER + "\n" + "y" * 9000)
    with pytest.raises(ValueError, match="cap"):
        deliberate_and_promote_proposal(
            prop.id, approver="adriano", evaluation=APPROVED_EVAL,
            event_store=store_env["store"],
        )


# ---------------------------------------------------------------------------
# B1-low — rollback marks the proposal rolled_back
# ---------------------------------------------------------------------------

def test_rollback_marks_originating_proposal_rolled_back(store_env):
    prop = _make_proposal(store_env, values_patch="mandate CRL checks")
    deliberate_and_promote_proposal(
        prop.id, approver="adriano", evaluation=APPROVED_EVAL,
        event_store=store_env["store"],
    )
    v1 = store_env["id_mgr"].list_versions("architect-bot")[0]
    res = rollback_bot_identity(
        "architect-bot", v1.id, reason="test rollback",
        event_store=store_env["store"],
    )
    assert res["rolled_back_proposal_id"] == prop.id
    fresh = {p.id: p for p in store_env["evo_mgr"].get_proposals()}
    assert fresh[prop.id].status == STATUS_ROLLED_BACK
    rolled_evts = [e for e in store_env["store"].get_all()
                   if e.name == "civ.evolution.proposal_rolled_back"]
    assert any(e.payload.get("proposal_id") == prop.id for e in rolled_evts)


def test_rollback_without_proposal_link_is_clean(store_env):
    """Rollback of a never-promoted bot must not fabricate proposal updates."""
    create_bot("security-bot", "security", event_store=store_env["store"])
    versions = store_env["id_mgr"].list_versions("security-bot")
    res = rollback_bot_identity(
        "security-bot", versions[0].id, event_store=store_env["store"],
    )
    assert res["rolled_back_proposal_id"] is None
    assert not [e for e in store_env["store"].get_all()
                if e.name == "civ.evolution.proposal_rolled_back"]


# ---------------------------------------------------------------------------
# Council-fachada — automated ratification recorded as metadata
# ---------------------------------------------------------------------------

def test_council_fachada_records_automated_ratification(store_env, caplog):
    import logging
    store = store_env["store"]
    prop = _make_proposal(store_env)
    env_c = store_env["council_mgr"]
    create_bot("security-bot", "security", event_store=store)
    env_c.register(CouncilSpec(
        id="oversight", purpose="Evolution oversight",
        members=["architect-bot", "security-bot"],
    ))
    with caplog.at_level(logging.WARNING, logger="hermes.platform.council.manager"):
        res = deliberate_and_promote_proposal(
            prop.id, council_id="oversight", approver="adriano",
            evaluation=APPROVED_EVAL, event_store=store,
        )
    decision = env_c.get_decision(res["council_decision_id"])
    assert decision is not None
    # Proveniência explícita: a rota humana de promoção é fachada declarada.
    assert decision.deliberation == "facade"
    assert decision.metadata.get("ratification") == "automated"
    positions = decision.positions
    assert positions, "council decision must carry member positions"
    for pos in positions.values():
        assert pos.get("ratification") == "automated"
    meta = store_env["id_mgr"].get_active_version("architect-bot").bundle.metadata
    assert meta["ratification"] == "automated"
    # A fachada também fica visível no log (WARNING), não só no evento.
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("facade" in msg for msg in warnings)
