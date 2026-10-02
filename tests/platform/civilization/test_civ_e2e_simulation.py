"""End-to-End Simulation of HAOS Civilization Multi-Agent Architecture.

Demonstrates and verifies the complete lifecycle across all 4 tiers:
- V1 Foundation: Persistent Bot identity, version freeze, Leaf derivation without SOUL mutation.
- V2 Society: Dynamic relationship graphs, evidence-based reputation, role assignments, specialist selection.
- V3 Evolution: Post-mission ExperienceEvents, EvolutionProposals, risk classification, version promotion, compensating rollback.
- V4 Civilization: Versioned constitution, hard-deny and advisory policy gates, institutional knowledge assertions.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from hermes.platform.observability.event_store import EventStore
from hermes.platform.bots.identity import (
    BotIdentityBundle,
    BotIdentitySpec,
    compute_bundle_hash,
    compute_sha256,
)
from hermes.platform.bots.spec import BotSpec
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.identity_resolver import IdentityResolver
from hermes.platform.bots.leaf_protocol import (
    build_temporary_soul,
    create_leaf_identity_snapshot,
)
from hermes.platform.bots.native_civ import (
    native_compute_bundle_hash,
    native_compute_sha256,
    native_build_temporary_soul,
)
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.spec import CouncilSpec
from hermes.platform.society.manager import (
    SelfEndorsementError,
    SocietyManager,
)
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    ExperienceEvent,
    RISK_LOW,
    RISK_MEDIUM,
    RISK_IDENTITY_CRITICAL,
    STATUS_APPROVED,
    STATUS_CANARY,
    STATUS_PROMOTED,
    STATUS_REVIEW,
    STATUS_ROLLED_BACK,
    StaleBaseVersionError,
)
from hermes.platform.civilization.manager import CivilizationManager
from hermes.platform.civilization.models import (
    ConstitutionRule,
    POLICY_RESULT_ALLOW,
    POLICY_RESULT_DENY,
    POLICY_RESULT_REQUIRE_APPROVAL,
    RULE_TYPE_ADVISORY,
    RULE_TYPE_HARD_DENY,
)


@pytest.fixture
def civ_environment():
    """Setup an isolated temporary environment for the civilization simulation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        db_path = tmppath / "civ_events.db"
        bots_dir = tmppath / "bots"
        bots_dir.mkdir(parents=True, exist_ok=True)

        event_store = EventStore(db_path=db_path)
        identity_resolver = IdentityResolver(root_dir=bots_dir)
        identity_manager = IdentityManager(event_store=event_store)
        council_manager = CouncilManager(event_store=event_store)
        society_manager = SocietyManager(event_store=event_store)
        evolution_manager = BotEvolutionManager(event_store=event_store)
        civ_manager = CivilizationManager(event_store=event_store)

        yield {
            "root": tmppath,
            "bots_dir": bots_dir,
            "event_store": event_store,
            "identity_resolver": identity_resolver,
            "identity_manager": identity_manager,
            "council_manager": council_manager,
            "society_manager": society_manager,
            "evolution_manager": evolution_manager,
            "civ_manager": civ_manager,
        }


def test_full_civilization_lifecycle_e2e(civ_environment):
    env = civ_environment
    bots_dir = env["bots_dir"]
    id_mgr: IdentityManager = env["identity_manager"]
    resolver: IdentityResolver = env["identity_resolver"]
    council_mgr: CouncilManager = env["council_manager"]
    society_mgr: SocietyManager = env["society_manager"]
    evo_mgr: BotEvolutionManager = env["evolution_manager"]
    civ_mgr: CivilizationManager = env["civ_manager"]

    # =========================================================================
    # STAGE 1: BOT IDENTITIES & CRYPTOGRAPHIC VERIFICATION (V1)
    # =========================================================================
    # Provision ArchitectBot
    arch_dir = bots_dir / "architect-bot"
    arch_dir.mkdir()
    arch_soul = "I am ArchitectBot, focused on distributed systems and high availability."
    arch_id_text = "ArchitectBot v1.0 - Core Infrastructure Designer"
    arch_values = "Resilience, simplicity, low latency, horizontal scalability."
    (arch_dir / "SOUL.md").write_text(arch_soul, encoding="utf-8")
    (arch_dir / "IDENTITY.md").write_text(arch_id_text, encoding="utf-8")
    (arch_dir / "VALUES.md").write_text(arch_values, encoding="utf-8")

    # Provision SecurityBot
    sec_dir = bots_dir / "security-bot"
    sec_dir.mkdir()
    sec_soul = "I am SecurityBot, verifying zero-trust, cryptographic bounds, and least privilege."
    sec_id_text = "SecurityBot v1.0 - Security and Policy Guardian"
    sec_values = "Zero trust, defense in depth, explicit authorization, privacy."
    (sec_dir / "SOUL.md").write_text(sec_soul, encoding="utf-8")
    (sec_dir / "IDENTITY.md").write_text(sec_id_text, encoding="utf-8")
    (sec_dir / "VALUES.md").write_text(sec_values, encoding="utf-8")

    # Resolve and register ArchitectBot
    arch_spec = BotSpec(
        id="architect-bot",
        name="ArchitectBot",
        identity=BotIdentitySpec(soul="SOUL.md", identity="IDENTITY.md", values="VALUES.md", version=1),
    )
    arch_bundle = resolver.resolve(arch_spec, bot_dir=arch_dir)
    assert arch_bundle.soul == arch_soul
    # Test dual-stack hash equivalence
    py_arch_hash = compute_bundle_hash(arch_bundle.soul, arch_bundle.identity, arch_bundle.values)
    native_arch_hash = native_compute_bundle_hash(arch_bundle.soul, arch_bundle.identity, arch_bundle.values)
    if native_arch_hash is not None:
        assert py_arch_hash == native_arch_hash
    arch_v1 = id_mgr.create_version("architect-bot", arch_bundle, activate=True)
    assert arch_v1.version == 1

    # Resolve and register SecurityBot
    sec_spec = BotSpec(
        id="security-bot",
        name="SecurityBot",
        identity=BotIdentitySpec(soul="SOUL.md", identity="IDENTITY.md", values="VALUES.md", version=1),
    )
    sec_bundle = resolver.resolve(sec_spec, bot_dir=sec_dir)
    sec_v1 = id_mgr.create_version("security-bot", sec_bundle, activate=True)
    assert sec_v1.version == 1

    # =========================================================================
    # STAGE 2: SOCIETY, REPUTATION VECTORS & SPECIALIST SELECTION (V2)
    # =========================================================================
    # Establish peer relationship
    rel = society_mgr.establish_relationship(
        from_bot="architect-bot",
        to_bot="security-bot",
        relation_type="collaborates_with",
        weight=0.9,
    )
    assert rel.from_bot == "architect-bot"
    assert rel.to_bot == "security-bot"

    # Self-endorsement must be rejected
    with pytest.raises(SelfEndorsementError):
        society_mgr.record_reputation(
            subject_bot="architect-bot",
            domain="architecture",
            evidence_ref="self-eval",
            outcome="success",
            delta_hint=0.5,
            actor_bot="architect-bot",
        )

    # Cross-bot endorsements based on domain contributions
    society_mgr.record_reputation(
        subject_bot="architect-bot",
        domain="architecture",
        evidence_ref="review-mesh-topo",
        outcome="success",
        delta_hint=0.35,
        actor_bot="security-bot",
    )
    society_mgr.record_reputation(
        subject_bot="security-bot",
        domain="security",
        evidence_ref="audit-crypto-keys",
        outcome="success",
        delta_hint=0.45,
        actor_bot="architect-bot",
    )

    # Verify specialist selection chooses highest domain reputation
    sec_specialists = society_mgr.select_specialists(
        candidate_bots=["architect-bot", "security-bot"],
        domain="security",
        count=1,
    )
    assert len(sec_specialists) == 1
    assert sec_specialists[0][0] == "security-bot"

    arch_specialists = society_mgr.select_specialists(
        candidate_bots=["architect-bot", "security-bot"],
        domain="architecture",
        count=1,
    )
    assert len(arch_specialists) == 1
    assert arch_specialists[0][0] == "architect-bot"

    # =========================================================================
    # STAGE 3: CIVILIZATION CONSTITUTION & POLICY GATE ENFORCEMENT (V4)
    # =========================================================================
    # Enact Constitution v1
    rule_format_hard_deny = ConstitutionRule(
        id="CONST-01",
        name="No Raw Disk Format",
        description="Forbidden raw partition or raw drive format operations",
        rule_type=RULE_TYPE_HARD_DENY,
        target_action="raw_disk_format",
    )
    rule_deploy_advisory = ConstitutionRule(
        id="CONST-02",
        name="Advisory Deploy Service",
        description="Advisory check on service network deployment",
        rule_type=RULE_TYPE_ADVISORY,
        target_action="deploy_service",
    )

    constitution = civ_mgr.enact_constitution(
        version=1,
        title="Civ Core Constitution v1",
        rules=[rule_format_hard_deny, rule_deploy_advisory],
        approved_by="civilization-founding-council",
    )
    assert constitution.version == 1

    # Verify hard-deny blocks illicit action immediately
    denied_decision = civ_mgr.evaluate_policy(
        subject_bot="architect-bot",
        action="raw_disk_format",
        resource="/dev/sda",
    )
    assert denied_decision.result == POLICY_RESULT_DENY
    assert denied_decision.rule_id == "CONST-01"

    # Verify advisory check requires approval
    advisory_decision = civ_mgr.evaluate_policy(
        subject_bot="architect-bot",
        action="deploy_service",
        resource="cluster-gateway",
    )
    assert advisory_decision.result == POLICY_RESULT_REQUIRE_APPROVAL
    assert advisory_decision.rule_id == "CONST-02"

    # Verify benign action not restricted by constitution passes freely
    allowed_decision = civ_mgr.evaluate_policy(
        subject_bot="architect-bot",
        action="read_topology",
        resource="cluster-network-map",
    )
    assert allowed_decision.result == POLICY_RESULT_ALLOW

    # =========================================================================
    # STAGE 4: COUNCIL DELIBERATION WITH DISSENT PRESERVATION (V1/V2)
    # =========================================================================
    council_spec = CouncilSpec(
        id="arch-sec-council",
        purpose="Review and approve distributed architecture changes with cryptographic guarantees",
        members=["architect-bot", "security-bot"],
        decision_mode="consensus_with_dissent",
    )
    council_mgr.register(council_spec)

    # Start deliberation session
    session = council_mgr.start_session(
        council_id="arch-sec-council",
        objective="Deploy mTLS authentication across cluster services with OAuth2 fallback.",
    )
    assert session.phase == "independent_analysis"

    # ArchitectBot submits position: Approve mTLS deployment
    council_mgr.submit_position(
        session_id=session.session_id,
        bot_id="architect-bot",
        position={"vote": "APPROVE", "reason": "mTLS provides strong hardware-backed identity and low latency."},
    )

    # SecurityBot submits position with explicit dissent
    council_mgr.submit_position(
        session_id=session.session_id,
        bot_id="security-bot",
        position={"vote": "APPROVE_WITH_DISSENT", "reason": "OAuth2 fallback requires mandatory token revocation lists."},
        dissent="OAuth2 fallback must strictly enforce 60s TTL and CRL checks, or breach exposure is heightened.",
    )

    # Synthesize positions into a DecisionRecord preserving dissent
    decision_record = council_mgr.record_decision(
        session_id=session.session_id,
        synthesis="Adopt mTLS as primary; implement OAuth2 fallback with mandatory 60s CRL validation as requested by SecurityBot.",
        decision="APPROVED_WITH_CONDITIONS",
        confidence=0.92,
        action_refs=["deploy-mtls-mesh"],
    )
    assert decision_record.decision == "APPROVED_WITH_CONDITIONS"
    assert "security-bot" in decision_record.dissent
    assert "OAuth2 fallback" in decision_record.dissent["security-bot"]

    # =========================================================================
    # STAGE 5: LEAF DERIVATION & EXECUTION WITHOUT SOUL MUTATION (V1)
    # =========================================================================
    # Spawn Leaf derived from ArchitectBot to execute the council-approved action
    leaf_snapshot = create_leaf_identity_snapshot(
        leaf_id="leaf-mtls-worker-01",
        parent_bot_id="architect-bot",
        identity_version=arch_v1,
        task_description="Generate configuration for Envoy mTLS proxy with OAuth2 CRL filter.",
        constraints=["Do not expose private keys in plaintext", "Ensure 60s token TTL"],
        council_id="arch-sec-council",
        council_session_id=session.session_id,
    )
    assert leaf_snapshot.parent_bot_id == "architect-bot"
    assert "Envoy mTLS proxy" in leaf_snapshot.temporary_soul
    assert "60s token TTL" in leaf_snapshot.temporary_soul

    # Execute Leaf simulation (generating artifact)
    config_output = {
        "envoy_cluster": "cluster_mtls",
        "tls_context": {"require_client_certificate": True},
        "oauth2_crl_ttl_seconds": 60,
    }
    artifact_path = env["root"] / "envoy_config.json"
    artifact_path.write_text(str(config_output), encoding="utf-8")

    # Invariant check: Parent Bot's SOUL.md on disk was NOT modified by Leaf execution
    current_arch_soul_on_disk = (arch_dir / "SOUL.md").read_text(encoding="utf-8")
    assert current_arch_soul_on_disk == arch_soul
    assert compute_sha256(current_arch_soul_on_disk) == compute_sha256(arch_soul)

    # =========================================================================
    # STAGE 6: EXPERIENCE GATHERING, EVOLUTION & COMPENSATING ROLLBACK (V3)
    # =========================================================================
    # Record experience event from the successful mission
    exp_event = evo_mgr.record_experience(
        bot_id="architect-bot",
        event_type="mission_completed",
        domain="architecture",
        summary="Successfully provisioned Envoy mTLS configuration meeting SecurityBot dissent constraints.",
        success=True,
        payload={"artifact": str(artifact_path), "council_id": "arch-sec-council"},
    )
    assert exp_event.success is True

    # Current active version of architect-bot
    active_v1 = id_mgr.get_active_version("architect-bot")
    assert active_v1 is not None

    # Create evolution proposal to incorporate OAuth2 CRL knowledge into ArchitectBot's values
    proposal = evo_mgr.create_proposal(
        bot_id="architect-bot",
        base_version_hash=active_v1.bundle_hash,
        risk_class=RISK_LOW,
        rationale="Learned from arch-sec-council deliberation that fallback auth requires explicit CRL verification.",
        proposed_values_patch="Always specify explicit revocation lists and short TTLs when fallback auth is used.",
        evidence_refs=[exp_event.id],
    )
    assert proposal.status == "draft"

    # Update proposal status through the governance FSM:
    # draft -> review -> approved -> canary -> promoted
    review_prop = evo_mgr.update_status(
        proposal_id=proposal.id,
        new_status=STATUS_REVIEW,
        current_bot_version_hash=active_v1.bundle_hash,
    )
    assert review_prop.status == STATUS_REVIEW

    approved_prop = evo_mgr.update_status(
        proposal_id=proposal.id,
        new_status=STATUS_APPROVED,
        current_bot_version_hash=active_v1.bundle_hash,
    )
    assert approved_prop.status == STATUS_APPROVED

    canary_prop = evo_mgr.update_status(
        proposal_id=proposal.id,
        new_status=STATUS_CANARY,
        current_bot_version_hash=active_v1.bundle_hash,
    )
    assert canary_prop.status == STATUS_CANARY

    promoted_prop = evo_mgr.update_status(
        proposal_id=proposal.id,
        new_status=STATUS_PROMOTED,
        current_bot_version_hash=active_v1.bundle_hash,
    )
    assert promoted_prop.status == STATUS_PROMOTED

    # In a full evolution cycle, the promoted proposal produces an updated IdentityVersion
    new_values = f"{arch_values}\n{proposal.proposed_values_patch}"
    new_bundle = BotIdentityBundle(
        soul=arch_soul,
        identity=arch_id_text,
        values=new_values,
        bot_id="architect-bot",
        bundle_hash=compute_bundle_hash(arch_soul, arch_id_text, new_values),
    )
    arch_v2 = id_mgr.create_version(
        bot_id="architect-bot",
        bundle=new_bundle,
        parent_id=active_v1.id,
        activate=True,
    )
    assert arch_v2.version == 2
    assert id_mgr.get_active_version("architect-bot").id == arch_v2.id

    # Verify stale base version detection: an older proposal with base_version_hash == active_v1.bundle_hash
    # will be rejected now that current active version hash is arch_v2.bundle_hash
    stale_proposal = evo_mgr.create_proposal(
        bot_id="architect-bot",
        base_version_hash=active_v1.bundle_hash,  # Now stale!
        risk_class=RISK_LOW,
        rationale="Stale proposal based on v1",
        proposed_values_patch="Some stale patch",
    )
    with pytest.raises(StaleBaseVersionError):
        # FSM: draft -> review is legal; the stale-base gate fires on the
        # first GATE transition (review -> approved) against v2.
        evo_mgr.update_status(
            proposal_id=stale_proposal.id,
            new_status=STATUS_REVIEW,
            current_bot_version_hash=arch_v2.bundle_hash,
        )
        evo_mgr.update_status(
            proposal_id=stale_proposal.id,
            new_status=STATUS_APPROVED,
            current_bot_version_hash=arch_v2.bundle_hash,
        )

    # Compensating rollback: Revert ArchitectBot active version back to v1
    id_mgr.activate_version("architect-bot", arch_v1.id)
    assert id_mgr.get_active_version("architect-bot").id == arch_v1.id
    rolled_back_prop = evo_mgr.update_status(
        proposal_id=proposal.id,
        new_status=STATUS_ROLLED_BACK,
        current_bot_version_hash=active_v1.bundle_hash,
    )
    assert rolled_back_prop.status == STATUS_ROLLED_BACK

    # =========================================================================
    # STAGE 7: CIVILIZATION SHARED MEMORY / KNOWLEDGE GRAPH (V4)
    # =========================================================================
    # Record institutional knowledge assertion
    assertion = civ_mgr.record_assertion(
        subject="cluster.auth.standard",
        predicate="enforces",
        object_="mTLS-with-OAuth2-CRL-60s",
        confidence=0.98,
        provenance_ref=decision_record.id,
    )
    assert assertion.subject == "cluster.auth.standard"
    assert assertion.confidence == 0.98

    # Query civilization memory
    results = civ_mgr.query_knowledge(subject="cluster.auth.standard")
    assert len(results) == 1
    assert results[0].object == "mTLS-with-OAuth2-CRL-60s"
    assert results[0].provenance_ref == decision_record.id
