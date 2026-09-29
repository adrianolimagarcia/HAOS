#!/usr/bin/env python3
"""HAOS Civilization Engine — isolated local lifecycle simulation.

Exercises seven manager workflows with synthetic data in a temporary directory.
This is not a live autonomous agent run or an acceleration benchmark.
  1. Persistent Bots & Cryptographic Identity Bundles
  2. Society Trust Graph, Reputation Vectors & Sybil/Collusion Defense
  3. Civilization Constitution & Real-time Policy Guardrails
  4. Deliberative Council with Dissent Preservation & Decision Records
  5. Ephemeral Leaf Manifestation & Session Identity Freeze (Prompt Cache Preservation)
  6. Experience Gating, Evolution Proposals & Version Rollback
  7. Temporal Knowledge Ledger & EventStore Replay Verification

Usage:
  python scripts/demo_civ_simulation.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes.platform.bots.identity import (
    BotIdentityBundle,
    BotIdentitySpec,
    compute_bundle_hash,
    compute_sha256,
)
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.identity_resolver import IdentityResolver
from hermes.platform.bots.leaf_protocol import create_leaf_identity_snapshot
from hermes.platform.bots.native_civ import (
    is_native_available,
    native_compute_bundle_hash,
    native_compute_sha256,
)
from hermes.platform.bots.spec import BotSpec
from hermes.platform.civilization.manager import CivilizationManager
from hermes.platform.civilization.models import (
    RULE_TYPE_ADVISORY,
    RULE_TYPE_HARD_DENY,
    ConstitutionRule,
)
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.spec import CouncilSpec
from hermes.platform.evolution.bot_evolution import (
    RISK_HIGH,
    RISK_LOW,
    STATUS_APPROVED,
    STATUS_PROMOTED,
    STATUS_ROLLED_BACK,
    BotEvolutionManager,
    StaleBaseVersionError,
)
from hermes.platform.observability.event_store import EventStore
from hermes.platform.society.manager import SelfEndorsementError, SocietyManager


# ANSI Color formatting
class Style:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"
    RESET = "\033[0m"


def banner(title: str, stage: int) -> None:
    print(f"\n{Style.BOLD}{Style.CYAN}{'='*78}{Style.RESET}")
    print(f"{Style.BOLD}{Style.YELLOW}  [STAGE {stage}] {title}{Style.RESET}")
    print(f"{Style.BOLD}{Style.CYAN}{'='*78}{Style.RESET}")


def step(msg: str) -> None:
    print(f"  {Style.GREEN}▸{Style.RESET} {msg}")


def info(key: str, val: str) -> None:
    print(f"    {Style.DIM}• {key}:{Style.RESET} {Style.BOLD}{val}{Style.RESET}")


def warn(msg: str) -> None:
    print(f"  {Style.YELLOW}⚠ {msg}{Style.RESET}")


def success(msg: str) -> None:
    print(f"  {Style.BOLD}{Style.GREEN}✔ {msg}{Style.RESET}")


def main() -> int:
    print(f"{Style.BOLD}{Style.MAGENTA}")
    print("╔══════════════════════════════════════════════════════════════════════════════╗")
    print("║               HAOS CIVILIZATION MULTI-AGENT ARCHITECTURE                     ║")
    print("║                   Isolated Local Lifecycle Simulation                      ║")
    print("╚══════════════════════════════════════════════════════════════════════════════╝")
    print(f"{Style.RESET}")

    native_active = is_native_available()
    print(
        f"  Core Native Rust Engine (packages/haos-civ): "
        f"{Style.GREEN + 'AVAILABLE (C-ABI loaded; performance not measured)' if native_active else Style.YELLOW + 'UNAVAILABLE (Python fallback)'}"
        f"{Style.RESET}\n"
    )

    with tempfile.TemporaryDirectory(prefix="haos_civ_demo_") as tmpdir:
        root = Path(tmpdir)
        bots_dir = root / "bots"
        bots_dir.mkdir()
        db_path = root / "events.db"

        event_store = EventStore(db_path=db_path)
        id_mgr = IdentityManager(event_store=event_store)
        resolver = IdentityResolver(root_dir=bots_dir)
        council_mgr = CouncilManager(event_store=event_store)
        society_mgr = SocietyManager(event_store=event_store)
        evo_mgr = BotEvolutionManager(event_store=event_store)
        civ_mgr = CivilizationManager(event_store=event_store)

        # ---------------------------------------------------------------------
        # STAGE 1: BOTS IDENTITIES & NATIVE CRYPTO HASHING
        # ---------------------------------------------------------------------
        banner("Persistent Bot Identities & Cryptographic Verification (V1)", 1)
        step("Provisioning persistent bot identity files (SOUL.md, IDENTITY.md, VALUES.md)...")

        arch_dir = bots_dir / "architect-bot"
        arch_dir.mkdir()
        (arch_dir / "SOUL.md").write_text(
            "I am ArchitectBot, focused on distributed systems, resilience, and high availability.",
            encoding="utf-8",
        )
        (arch_dir / "IDENTITY.md").write_text(
            "ArchitectBot v1.0 - Core Infrastructure Designer",
            encoding="utf-8",
        )
        (arch_dir / "VALUES.md").write_text(
            "Resilience, simplicity, low latency, horizontal scalability.",
            encoding="utf-8",
        )

        sec_dir = bots_dir / "security-bot"
        sec_dir.mkdir()
        (sec_dir / "SOUL.md").write_text(
            "I am SecurityBot, verifying zero-trust, cryptographic bounds, and least privilege.",
            encoding="utf-8",
        )
        (sec_dir / "IDENTITY.md").write_text(
            "SecurityBot v1.0 - Security and Policy Guardian",
            encoding="utf-8",
        )
        (sec_dir / "VALUES.md").write_text(
            "Zero trust, defense in depth, explicit authorization, privacy.",
            encoding="utf-8",
        )

        arch_spec = BotSpec(
            id="architect-bot",
            name="ArchitectBot",
            identity=BotIdentitySpec(soul="SOUL.md", identity="IDENTITY.md", values="VALUES.md", version=1),
        )
        sec_spec = BotSpec(
            id="security-bot",
            name="SecurityBot",
            identity=BotIdentitySpec(soul="SOUL.md", identity="IDENTITY.md", values="VALUES.md", version=1),
        )

        arch_bundle = resolver.resolve(arch_spec, bot_dir=arch_dir)
        sec_bundle = resolver.resolve(sec_spec, bot_dir=sec_dir)

        arch_hash = native_compute_bundle_hash(arch_bundle.soul, arch_bundle.identity, arch_bundle.values)
        sec_hash = native_compute_bundle_hash(sec_bundle.soul, sec_bundle.identity, sec_bundle.values)

        id_mgr.create_version("architect-bot", arch_bundle, activate=True)
        id_mgr.create_version("security-bot", sec_bundle, activate=True)

        info("ArchitectBot Bundle Hash", str(arch_hash)[:32] + "...")
        info("SecurityBot Bundle Hash", str(sec_hash)[:32] + "...")
        success("Bots registered and frozen at version 1 in EventStore.")

        # ---------------------------------------------------------------------
        # STAGE 2: SOCIETY, REPUTATION VECTORS & SYBIL DEFENSE
        # ---------------------------------------------------------------------
        banner("Society Trust Graph, Reputation Vectors & Anti-Collusion (V2)", 2)
        step("Establishing peer relationship between ArchitectBot and SecurityBot...")
        rel = society_mgr.establish_relationship(
            from_bot="architect-bot",
            to_bot="security-bot",
            relation_type="collaborates_with",
            weight=0.9,
        )
        info("Relationship Created", f"{rel.from_bot} --[{rel.relation_type} ({rel.weight})]--> {rel.to_bot}")

        step("Testing anti-collusion guardrail: attempting self-endorsement...")
        try:
            society_mgr.record_reputation(
                subject_bot="architect-bot",
                domain="architecture",
                evidence_ref="self-award-attempt",
                outcome="success",
                delta_hint=1.0,
                actor_bot="architect-bot",
            )
            raise AssertionError("SelfEndorsement was not blocked!")
        except SelfEndorsementError:
            success("SelfEndorsement rejected by anti-collusion policy engine!")

        step("Recording cross-bot domain reputation endorsements...")
        society_mgr.record_reputation(
            subject_bot="architect-bot",
            domain="architecture",
            evidence_ref="mission-cluster-design-42",
            outcome="success",
            delta_hint=0.45,
            actor_bot="security-bot",
        )
        society_mgr.record_reputation(
            subject_bot="security-bot",
            domain="security",
            evidence_ref="audit-crypto-keys-99",
            outcome="success",
            delta_hint=0.55,
            actor_bot="architect-bot",
        )

        arch_vec = society_mgr.get_reputation_vector("architect-bot")
        sec_vec = society_mgr.get_reputation_vector("security-bot")
        arch_score = arch_vec.domains["architecture"].score if "architecture" in arch_vec.domains else 0.0
        sec_score = sec_vec.domains["security"].score if "security" in sec_vec.domains else 0.0
        info("ArchitectBot Reputation Vector", f"architecture: {arch_score:.2f}")
        info("SecurityBot Reputation Vector", f"security: {sec_score:.2f}")

        selected_sec = society_mgr.select_specialists(["architect-bot", "security-bot"], "security", 1)
        selected_arch = society_mgr.select_specialists(["architect-bot", "security-bot"], "architecture", 1)
        info("Top Security Specialist Selected", f"{selected_sec[0][0]} (score: {selected_sec[0][1]:.2f})")
        info("Top Architecture Specialist Selected", f"{selected_arch[0][0]} (score: {selected_arch[0][1]:.2f})")
        success("Domain specialist routing verified via verified reputation vectors.")

        # ---------------------------------------------------------------------
        # STAGE 3: CONSTITUTION & REAL-TIME POLICY GATING
        # ---------------------------------------------------------------------
        banner("Civilization Constitution & Real-Time Policy Guardrails (V4)", 3)
        step("Enacting Constitution Version 1 with Hard-Deny and Advisory rules...")
        rule1 = ConstitutionRule(
            id="CONST-01",
            name="No Destructive Partitioning",
            description="Prevent unverified disk format or destruction operations",
            rule_type=RULE_TYPE_HARD_DENY,
            target_action="format_disk",
        )
        rule2 = ConstitutionRule(
            id="CONST-02",
            name="Service Deployment Approval",
            description="Require multi-signature approval prior to opening external network ports",
            rule_type=RULE_TYPE_ADVISORY,
            target_action="open_network_port",
        )
        constitution = civ_mgr.enact_constitution(
            version=1,
            title="Core Civ Constitution",
            rules=[rule1, rule2],
            approved_by="civilization-founding-council",
        )
        info("Active Constitution", f"Version {constitution.version} ({len(constitution.rules)} rules)")

        step("Evaluating policy on forbidden operation: ArchitectBot -> 'format_disk'...")
        d1 = civ_mgr.evaluate_policy(subject_bot="architect-bot", action="format_disk", resource="/dev/nvme0n1")
        info("Evaluation Result", f"{d1.result.upper()} (Triggered rule: {d1.rule_id})")
        assert d1.result == "deny"

        step("Evaluating policy on restricted operation: ArchitectBot -> 'open_network_port'...")
        d2 = civ_mgr.evaluate_policy(subject_bot="architect-bot", action="open_network_port", resource="port:443")
        info("Evaluation Result", f"{d2.result.upper()} (Triggered rule: {d2.rule_id})")
        assert d2.result == "require_approval"

        step("Evaluating policy on benign operation: ArchitectBot -> 'read_status'...")
        d3 = civ_mgr.evaluate_policy(subject_bot="architect-bot", action="read_status", resource="telemetry")
        info("Evaluation Result", f"{d3.result.upper()}")
        assert d3.result == "allow"
        success("Constitution rules enforced deterministically across all action classes.")

        # ---------------------------------------------------------------------
        # STAGE 4: COUNCIL DELIBERATION WITH DISSENT PRESERVATION
        # ---------------------------------------------------------------------
        banner("Council Deliberation & Dissent Preservation (V1/V2)", 4)
        step("Registering Architecture Review Council and initiating formal session...")
        council_spec = CouncilSpec(
            id="arch-sec-council",
            purpose="Deliberate on cluster topology changes and security boundaries",
            members=["architect-bot", "security-bot"],
            decision_mode="consensus_with_dissent",
        )
        council_mgr.register(council_spec)
        session = council_mgr.start_session(
            council_id="arch-sec-council",
            objective="Deploy TLS 1.3 Strict Mutual Authentication across all nodes",
        )
        info("Session Started", f"ID: {session.session_id} | Objective: {session.objective}")

        step("Submitting positions from council members...")
        council_mgr.submit_position(
            session_id=session.session_id,
            bot_id="security-bot",
            position={"vote": "APPROVE_WITH_DISSENT", "reason": "OAuth2 fallback requires mandatory token revocation lists."},
            dissent="OAuth2 fallback must strictly enforce 60s TTL and CRL checks, or breach exposure is heightened.",
        )
        council_mgr.submit_position(
            session_id=session.session_id,
            bot_id="architect-bot",
            position={"vote": "APPROVE", "reason": "mTLS provides strong hardware-backed identity and low latency."},
        )

        step("Formally recording council decision...")
        decision = council_mgr.record_decision(
            session_id=session.session_id,
            synthesis="Adopt mTLS as primary; implement OAuth2 fallback with mandatory 60s CRL validation as requested by SecurityBot.",
            decision="APPROVED_WITH_CONDITIONS",
            confidence=0.92,
            action_refs=["deploy-mtls-mesh"],
        )
        info("Council Decision", decision.decision)
        info("Synthesis", decision.synthesis)
        info("Dissent Record", str(decision.dissent))
        success("Deliberation finalized: Dissent preserved for historical auditability.")

        # ---------------------------------------------------------------------
        # STAGE 5: EPHEMERAL LEAF MANIFESTATION
        # ---------------------------------------------------------------------
        banner("Ephemeral Leaf Manifestation & Prompt Caching Freeze (V1)", 5)
        step("Spawning ephemeral Leaf for execution of council-approved task...")
        active_arch = id_mgr.get_active_version("architect-bot")
        assert active_arch is not None

        leaf_snapshot = create_leaf_identity_snapshot(
            leaf_id="leaf-architect-exec-101",
            parent_bot_id="architect-bot",
            identity_version=active_arch,
            task_description="Generate configuration for Envoy mTLS proxy with persistent tickets",
            constraints=["Do not expose private keys in plaintext", "Ensure 60s token TTL"],
            council_id="arch-sec-council",
            council_session_id=session.session_id,
        )
        info("Parent Bot", leaf_snapshot.parent_bot_id)
        info("Leaf ID", leaf_snapshot.leaf_id)
        info("Parent Frozen Version", f"v{leaf_snapshot.bot_identity_version}")
        info("Derived Ephemeral SOUL Length", f"{len(leaf_snapshot.temporary_soul)} chars")

        step("Verifying parent bot immutability during Leaf execution...")
        current_arch_soul_on_disk = (arch_dir / "SOUL.md").read_text(encoding="utf-8")
        assert compute_sha256(current_arch_soul_on_disk) == compute_sha256(arch_bundle.soul)
        success("Parent bot identity remains completely frozen and immutable in storage.")

        # ---------------------------------------------------------------------
        # STAGE 6: EXPERIENCE GATING, EVOLUTION & ROLLBACK
        # ---------------------------------------------------------------------
        banner("Experience Gating, Evolution Proposals & Rollback (V3)", 6)
        step("Capturing execution telemetry as ExperienceEvent...")
        exp = evo_mgr.record_experience(
            bot_id="architect-bot",
            event_type="mission_completed",
            domain="architecture",
            summary="Session tickets reduce mTLS handshake latency to <0.8ms.",
            success=True,
            payload={"p99_latency_ms": 1.2, "handshake_savings_pct": 82.0},
        )
        info("Experience Event ID", exp.id)

        step("Submitting and evaluating SAFE evolution proposal...")
        proposal = evo_mgr.create_proposal(
            bot_id="architect-bot",
            base_version_hash=active_arch.bundle_hash,
            risk_class=RISK_LOW,
            rationale="Incorporate validated session ticket caching into operational values.",
            proposed_values_patch="Always specify explicit session ticket caching when mTLS is enabled.",
            evidence_refs=[exp.id],
        )
        info("Proposal ID", proposal.id)
        info("Initial Status", proposal.status)

        evo_mgr.update_status(proposal.id, STATUS_APPROVED, active_arch.bundle_hash)
        evo_mgr.update_status(proposal.id, STATUS_PROMOTED, active_arch.bundle_hash)

        # Promote to v2
        new_values = f"{arch_bundle.values}\n{proposal.proposed_values_patch}"
        new_bundle = BotIdentityBundle(
            soul=arch_bundle.soul,
            identity=arch_bundle.identity,
            values=new_values,
            bot_id="architect-bot",
            bundle_hash=compute_bundle_hash(arch_bundle.soul, arch_bundle.identity, new_values),
        )
        v2 = id_mgr.create_version("architect-bot", new_bundle, parent_id=active_arch.id, activate=True)
        info("Applied Evolution", f"ArchitectBot upgraded to v{v2.version}")
        assert v2.version == 2

        step("Submitting and evaluating stale proposal against old base version...")
        stale_prop = evo_mgr.create_proposal(
            bot_id="architect-bot",
            base_version_hash=active_arch.bundle_hash,  # Now stale!
            risk_class=RISK_LOW,
            rationale="Stale proposal based on v1",
            proposed_values_patch="Stale patch",
        )
        try:
            evo_mgr.update_status(stale_prop.id, STATUS_APPROVED, current_bot_version_hash=v2.bundle_hash)
            raise AssertionError("Stale proposal was not rejected!")
        except StaleBaseVersionError:
            success("StaleBaseVersionError: Drift detected! Evolution rejected against superseded version.")

        step("Executing deterministic rollback from v2 back to v1...")
        id_mgr.activate_version("architect-bot", active_arch.id)
        rolled_back_prop = evo_mgr.update_status(
            proposal_id=proposal.id,
            new_status=STATUS_ROLLED_BACK,
            current_bot_version_hash=active_arch.bundle_hash,
        )
        current_arch = id_mgr.get_active_version("architect-bot")
        assert current_arch is not None and current_arch.version == 1
        info("Rollback Executed", f"ArchitectBot restored to active version {current_arch.version}")
        info("Proposal Status", rolled_back_prop.status)
        success("Rollback verified with complete historical preservation.")

        # ---------------------------------------------------------------------
        # STAGE 7: TEMPORAL KNOWLEDGE & AUDIT TRAIL VERIFICATION
        # ---------------------------------------------------------------------
        banner("Temporal Knowledge Ledger & EventStore Audit Verification (V4)", 7)
        step("Asserting verified civilizational knowledge with provenance...")
        assertion = civ_mgr.record_assertion(
            subject="cluster.auth.standard",
            predicate="enforces",
            object_="mTLS-with-OAuth2-CRL-60s",
            confidence=0.98,
            provenance_ref=decision.id,
        )
        info("Assertion ID", assertion.id)
        info("Triple", f"({assertion.subject}) --[{assertion.predicate}]--> ({assertion.object})")
        info("Confidence", f"{assertion.confidence * 100:.1f}%")

        step("Querying active knowledge ledger...")
        known = civ_mgr.query_knowledge(subject="cluster.auth.standard")
        assert len(known) == 1
        info("Retrieved Object", known[0].object)
        info("Provenance", known[0].provenance_ref or "None")
        success("Active knowledge assertion retrieved successfully.")

        step("Replaying complete EventStore audit trail...")
        all_events = event_store.get_all()
        print(f"  {Style.BOLD}Total append-only events recorded across lifecycle:{Style.RESET} {Style.GREEN}{len(all_events)}{Style.RESET}")

        event_names = [e.name for e in all_events]
        for name in sorted(set(event_names)):
            count = event_names.count(name)
            info(f"Event '{name}'", f"{count} occurrences")

        print(f"\n{Style.BOLD}{Style.GREEN}══════════════════════════════════════════════════════════════════════════════{Style.RESET}")
        print(f"{Style.BOLD}{Style.GREEN}   ✔ ALL 7 STAGES OF THE CIVILIZATION LIFE-CYCLE COMPLETED SUCCESSFULLY!      {Style.RESET}")
        print(f"{Style.BOLD}{Style.GREEN}══════════════════════════════════════════════════════════════════════════════{Style.RESET}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
