"""Comprehensive E2E tests for HAOS Civilization delegation pipeline.

Covers:
- A1: Bot, Identity, Leaf and Council integration in an isolated profile.
- A4: Constitutional policy enforcement (hard-deny, advisory approval) & memory enrichment.
- A2: Society reputation updates driven by execution outcomes.
- A3: Versioned evolution promotion with Council governance & compensating rollback.
- R: Recovery reconciliation for orphaned leaves and crashed workers.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from hermes.platform.bots.identity import BotIdentityBundle, BotIdentitySpec
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.spec import BotSpec
from hermes.platform.civilization.delegation import (
    create_bot,
    deliberate_and_promote_proposal,
    reconcile_civilization_state,
    record_task_result,
    rollback_bot_identity,
    route_task,
)
from hermes.platform.civilization.manager import CivilizationManager
from hermes.platform.civilization.models import (
    ConstitutionRule,
    KnowledgeAssertion,
    RULE_TYPE_ADVISORY,
    RULE_TYPE_HARD_DENY,
)
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.spec import CouncilSpec
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    RISK_LOW,
    STATUS_PROMOTED,
)
from hermes.platform.observability.event_store import EventStore
from hermes.platform.shadow_leaf import ShadowLeaf, ShadowLeafManager
from hermes.platform.society.manager import SocietyManager


@pytest.fixture
def civ_isolated_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db_path = root / "events.db"
        store = EventStore(db_path=db_path)
        yield {
            "root": root,
            "store": store,
            "bot_mgr": BotSpecManager(store),
            "id_mgr": IdentityManager(store),
            "civ_mgr": CivilizationManager(store),
            "council_mgr": CouncilManager(store),
            "society_mgr": SocietyManager(store),
            "evo_mgr": BotEvolutionManager(store),
        }


def test_a4_constitutional_hard_deny_and_advisory(civ_isolated_env):
    """A4: Constitution strictly halts unauthorized execution before run."""
    env = civ_isolated_env
    store = env["store"]
    civ_mgr: CivilizationManager = env["civ_mgr"]

    # Register specialist
    create_bot("security-bot", "security", description="Security Guardian", event_store=store)

    # Enact Constitution
    r1 = ConstitutionRule(
        id="CONST-DENY-RAW",
        name="No Raw Disk Access",
        description="Raw disk write operations are forbidden",
        rule_type=RULE_TYPE_HARD_DENY,
        target_action="raw_disk_write",
    )
    r2 = ConstitutionRule(
        id="CONST-ADV-PROD",
        name="Require Approval For Production Changes",
        description="Production deployments require explicit signoff",
        rule_type=RULE_TYPE_ADVISORY,
        target_action="deploy_prod",
    )
    civ_mgr.enact_constitution(version=1, title="Test Constitution", rules=[r1, r2], approved_by="admin")

    # Hard-deny fails closed immediately
    with pytest.raises(PermissionError) as exc_info:
        route_task({
            "bot_id": "security-bot",
            "goal": "Write raw blocks to disk",
            "action": "raw_disk_write",
            "resource": "/dev/nvme0n1",
        }, event_store=store)
    assert "CONST-DENY-RAW" in str(exc_info.value)

    # Verify denial event was recorded in event store
    failed_evts = [e for e in store.get_all() if e.name == "civ.leaf.failed"]
    assert len(failed_evts) == 1
    assert failed_evts[0].payload["reason"] == "denied_by_constitution:CONST-DENY-RAW"

    # Advisory check fails without approval
    with pytest.raises(PermissionError) as exc_info:
        route_task({
            "bot_id": "security-bot",
            "goal": "Deploy production mesh",
            "action": "deploy_prod",
            "resource": "prod-cluster",
            "approved": False,
        }, event_store=store)
    assert "CONST-ADV-PROD" in str(exc_info.value)

    # Advisory check succeeds when explicitly approved
    task = route_task({
        "bot_id": "security-bot",
        "goal": "Deploy production mesh",
        "action": "deploy_prod",
        "resource": "prod-cluster",
        "approved": True,
    }, event_store=store)
    assert task["bot_id"] == "security-bot"


def test_a4_institutional_memory_enrichment(civ_isolated_env):
    """A4: Memory assertions are retrieved and injected as execution context."""
    env = civ_isolated_env
    store = env["store"]
    civ_mgr: CivilizationManager = env["civ_mgr"]

    # Assert institutional knowledge
    civ_mgr.record_assertion(
        subject="OAuth2",
        predicate="enforces",
        object_="strict CRL validation and 60s max token TTL",
        confidence=0.98,
        provenance_ref="council-decision-42",
    )

    create_bot("security-bot", "security", description="Security bot", event_store=store)
    task = route_task({
        "bot_id": "security-bot",
        "goal": "Configure OAuth2 fallback for authentication cluster",
    }, event_store=store)

    assert "Institutional Knowledge" in task.get("context", "")
    assert "strict CRL validation" in task.get("context", "")


def test_a1_bot_identity_leaf_and_council_integration(civ_isolated_env):
    """A1: Bot, identity, Leaf snapshot and Council session integrated in a real task."""
    env = civ_isolated_env
    store = env["store"]
    council_mgr: CouncilManager = env["council_mgr"]

    # Setup specialist bots
    create_bot("architect-bot", "architecture", description="Core architect", event_store=store)
    create_bot("security-bot", "security", description="Security guardian", event_store=store)

    # Register council
    cspec = CouncilSpec(
        id="arch-sec-council",
        purpose="Infrastructure governance",
        members=["architect-bot", "security-bot"],
        decision_mode="consensus_with_dissent",
    )
    council_mgr.register(cspec)

    # Start a council session
    csession = council_mgr.start_session("arch-sec-council", objective="Deploy service mesh with mTLS")

    # Route task linked to the council session
    task = route_task({
        "bot_id": "architect-bot",
        "goal": "Generate Envoy proxy configuration with mTLS",
        "council_id": "arch-sec-council",
        "council_session_id": csession.session_id,
        "constraints": ["Zero plaintext credentials", "Verify certificate rotation"],
    }, event_store=store)

    assert task["bot_id"] == "architect-bot"
    assert task["identity_version"] == 1
    assert "leaf_snapshot" in task
    snapshot = task["leaf_snapshot"]
    assert snapshot["parent_bot_id"] == "architect-bot"
    assert snapshot["council_id"] == "arch-sec-council"
    assert snapshot["council_session_id"] == csession.session_id
    assert "Zero plaintext credentials" in snapshot["temporary_soul"]

    # Record task completion
    record_task_result(task, {
        "status": "completed",
        "summary": "Generated Envoy config with mTLS successfully verified",
    }, event_store=store)

    names = [e.name for e in store.get_all()]
    assert "civ.leaf.created" in names
    assert "civ.leaf.completed" in names


def test_a2_society_reputation_from_real_execution(civ_isolated_env):
    """A2: Execution results feed directly into society reputation with cross-bot attribution."""
    env = civ_isolated_env
    store = env["store"]
    soc_mgr: SocietyManager = env["society_mgr"]

    create_bot("security-bot", "security", description="Security bot", event_store=store)
    create_bot("architect-bot", "architecture", description="Architect bot", event_store=store)

    # Architect delegates a task to SecurityBot
    task = route_task({
        "bot_id": "security-bot",
        "parent_bot_id": "architect-bot",
        "goal": "Audit TLS certificates",
    }, event_store=store)

    record_task_result(task, {
        "status": "completed",
        "summary": "Audit passed: all certificates match 2048+ bit RSA / P-256",
    }, event_store=store)

    # Verify reputation was recorded with architect-bot as actor
    rep_evts = [e for e in store.get_all() if e.name == "civ.society.reputation-event-recorded"]
    assert len(rep_evts) >= 1
    latest_rep = rep_evts[-1].payload["reputation"]
    assert latest_rep["subject_bot"] == "security-bot"
    assert latest_rep["actor_bot"] == "architect-bot"
    assert latest_rep["outcome"] == "success"
    assert latest_rep["delta_hint"] > 0

    reps = soc_mgr.get_all_reputations()
    assert "security-bot" in reps
    assert "security" in reps["security-bot"].domains
    assert reps["security-bot"].overall_score > 0


def test_a3_evolution_governed_promotion_and_rollback(civ_isolated_env):
    """A3: Versioned evolution proposal approved by Council and promoted into immutable IdentityVersion."""
    env = civ_isolated_env
    store = env["store"]
    id_mgr: IdentityManager = env["id_mgr"]
    evo_mgr: BotEvolutionManager = env["evo_mgr"]
    council_mgr: CouncilManager = env["council_mgr"]

    create_bot("architect-bot", "architecture", description="Architect", event_store=store)
    create_bot("security-bot", "security", description="Security", event_store=store)

    council_mgr.register(CouncilSpec(
        id="arch-sec-council",
        purpose="Evolution oversight",
        members=["architect-bot", "security-bot"],
    ))

    active_v1 = id_mgr.get_active_version("architect-bot")
    assert active_v1.version == 1

    # Record mission experience
    exp = evo_mgr.record_experience(
        bot_id="architect-bot",
        event_type="mission_completed",
        domain="architecture",
        summary="Learned to require strict CRL checks during OAuth2 fallbacks",
        success=True,
    )

    # Create proposal
    prop = evo_mgr.create_proposal(
        bot_id="architect-bot",
        base_version_hash=active_v1.bundle_hash,
        risk_class=RISK_LOW,
        rationale="Update values with CRL fallback rules learned from mission experience",
        proposed_values_patch="Always mandate CRL checks when fallback auth is active.",
        evidence_refs=[exp.id],
    )

    # Deliberate and promote through Council.
    # Hardened route (audit B1): a named human approver is mandatory and a
    # low-risk promotion must cite an accepted evaluation verdict; without
    # these the call now fails closed with PermissionError.
    promo_result = deliberate_and_promote_proposal(
        proposal_id=prop.id,
        council_id="arch-sec-council",
        decision_summary="Council unanimously approves CRL values addition",
        approver="operator-adriano",
        evaluation={"accepted": True, "reason": "held-out suite passed"},
        event_store=store,
    )

    assert promo_result["new_version_number"] == 2
    assert promo_result["approver"] == "operator-adriano"
    active_v2 = id_mgr.get_active_version("architect-bot")
    assert active_v2.version == 2
    assert "mandate CRL checks" in active_v2.bundle.values

    # Test rollback — B1-low: the originating proposal must be marked rolled_back
    roll_result = rollback_bot_identity(
        bot_id="architect-bot",
        target_version_id=active_v1.id,
        reason="Test rollback",
        event_store=store,
    )
    assert roll_result["version_number"] == 3
    assert roll_result["rolled_back_proposal_id"] == prop.id
    active_v3 = id_mgr.get_active_version("architect-bot")
    assert active_v3.version == 3
    assert "mandate CRL checks" not in active_v3.bundle.values
    rolled_prop = {p.id: p for p in evo_mgr.get_proposals()}[prop.id]
    assert rolled_prop.status == "rolled_back"


def test_r_recovery_and_reconciliation(civ_isolated_env):
    """R: Reconciliation marks orphaned leaves as failed and preserves consistent state."""
    env = civ_isolated_env
    store = env["store"]
    root = env["root"]
    shadows_dir = root / "shadows"
    shadows_dir.mkdir(parents=True, exist_ok=True)

    create_bot("security-bot", "security", description="Security bot", event_store=store)

    leaf_mgr = ShadowLeafManager(base_repo_dir=root, shadows_root=shadows_dir, event_store=store)
    # Simulate an orphaned leaf whose worker crashed/died
    leaf = ShadowLeaf(
        leaf_id="shadow-security-bot-crashed",
        parent_bot_id="security-bot",
        parent_bot_name="SecurityBot",
        profile="default",
        task_description="Long running audit that was interrupted",
        worktree_path=str(shadows_dir / "shadow-security-bot-crashed"),
        branch_name="shadow/shadow-security-bot-crashed",
        status="active",
        created_at=time.time() - 4000.0,  # timed out
    )
    leaf_mgr._leaves[leaf.leaf_id] = leaf
    leaf_mgr._save_registry()

    # Reconcile state
    rec_result = reconcile_civilization_state(
        base_repo_dir=root,
        shadows_root=shadows_dir,
        event_store=store,
        max_age_seconds=100.0,
    )

    assert "shadow-security-bot-crashed" in rec_result["reconciled_orphaned_leaves"]
    assert rec_result["total_active_bots"] == 1
    assert rec_result["bot_versions"]["security-bot"] == 1

    # Reload leaf registry and verify status is failed
    fresh_leaf_mgr = ShadowLeafManager(base_repo_dir=root, shadows_root=shadows_dir, event_store=store)
    reloaded_leaf = fresh_leaf_mgr.get_shadow("shadow-security-bot-crashed")
    assert reloaded_leaf.status == "failed"
    assert reloaded_leaf.exit_code == 1

    # Verify failure event was appended
    failed_names = [e.name for e in store.get_all() if e.name == "civ.leaf.failed"]
    assert len(failed_names) >= 1
