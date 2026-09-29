#!/usr/bin/env python3
"""Bootstrap script to initialize persistent HAOS Civilization state in HERMES_HOME.

Provisions persistent bots (architect-bot, security-bot), registers identity versions,
enacts the initial constitution, registers deliberative councils, records cross-bot
reputation vectors, and documents civilizational governance in the canonical EventStore.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from hermes_constants import get_hermes_home
from hermes.platform.observability.event_store import EventStore
from hermes.platform.bots.spec import BotSpec, BotIdentitySpec
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.identity_resolver import IdentityResolver
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.identity import compute_bundle_hash
from hermes.platform.society.manager import SocietyManager
from hermes.platform.civilization.manager import CivilizationManager
from hermes.platform.civilization.models import (
    ConstitutionRule,
    RULE_TYPE_HARD_DENY,
    RULE_TYPE_ADVISORY,
)
from hermes.platform.council.spec import CouncilSpec
from hermes.platform.council.manager import CouncilManager
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    RISK_LOW,
    STATUS_APPROVED,
)
from hermes.platform.bots.leaf_protocol import create_leaf_identity_snapshot
from hermes.platform.shadow_leaf import ShadowLeafManager


def main():
    home = get_hermes_home()
    print(f"[*] Bootstrapping HAOS Civilization into {home}...")

    bots_dir = home / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)

    event_db = home / "events.db"
    store = EventStore(event_db)

    bot_mgr = BotSpecManager(store)
    id_mgr = IdentityManager(store)
    resolver = IdentityResolver(root_dir=bots_dir)
    society_mgr = SocietyManager(store)
    civ_mgr = CivilizationManager(store)
    council_mgr = CouncilManager(store)
    evo_mgr = BotEvolutionManager(store)

    # 1. Provision Bot Files
    arch_dir = bots_dir / "architect-bot"
    arch_dir.mkdir(exist_ok=True)
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
    sec_dir.mkdir(exist_ok=True)
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

    # 2. Register Bot Specs & Identities
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

    bot_mgr.register(arch_spec)
    bot_mgr.register(sec_spec)

    arch_bundle = resolver.resolve(arch_spec, bot_dir=arch_dir)
    sec_bundle = resolver.resolve(sec_spec, bot_dir=sec_dir)

    arch_ver = id_mgr.get_active_version("architect-bot")
    if not arch_ver:
        arch_ver = id_mgr.create_version("architect-bot", arch_bundle, activate=True)
    sec_ver = id_mgr.get_active_version("security-bot")
    if not sec_ver:
        sec_ver = id_mgr.create_version("security-bot", sec_bundle, activate=True)

    print(f"[+] Bots registered: architect-bot (v{arch_ver.version}), security-bot (v{sec_ver.version})")

    # 3. Society & Reputation Vectors
    society_mgr.establish_relationship(
        from_bot="architect-bot",
        to_bot="security-bot",
        relation_type="collaborates_with",
        weight=0.9,
    )
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
    print("[+] Society relationships and reputation vectors recorded.")

    # 4. Constitution & Policy Rules
    active_const = civ_mgr.get_active_constitution()
    if not active_const:
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
        active_const = civ_mgr.enact_constitution(
            version=1,
            title="Core HAOS Constitution",
            rules=[rule1, rule2],
            approved_by="civilization-founding-council",
        )
    print(f"[+] Active constitution: {active_const.title} (v{active_const.version})")

    # 5. Deliberative Council
    council = council_mgr.get("arch-sec-council")
    if not council:
        council_spec = CouncilSpec(
            id="arch-sec-council",
            purpose="Deliberate on cluster topology changes and security boundaries",
            members=["architect-bot", "security-bot"],
            decision_mode="consensus_with_dissent",
        )
        council_mgr.register(council_spec)

    sessions = [s for s in council_mgr._state()[1].values() if s.council_id == "arch-sec-council"]
    if not sessions:
        session = council_mgr.start_session(
            council_id="arch-sec-council",
            objective="Deploy TLS 1.3 Strict Mutual Authentication across all nodes",
        )
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
        decision = council_mgr.record_decision(
            session_id=session.session_id,
            synthesis="Adopt mTLS as primary; implement OAuth2 fallback with mandatory 60s CRL validation as requested by SecurityBot.",
            decision="APPROVED_WITH_CONDITIONS",
            confidence=0.92,
            action_refs=["deploy-mtls-mesh"],
        )
        print(f"[+] Council deliberation recorded: Decision {decision.decision}")
    else:
        session = sessions[0]

    # 6. Ephemeral Leaf Execution
    shadow_root = home / "shadows"
    shadow_root.mkdir(exist_ok=True)
    leaf_mgr = ShadowLeafManager(base_repo_dir=Path.cwd(), event_store=store)
    leaf_snapshot = create_leaf_identity_snapshot(
        leaf_id="leaf-architect-exec-101",
        parent_bot_id="architect-bot",
        identity_version=arch_ver,
        task_description="Generate configuration for Envoy mTLS proxy with persistent tickets",
        constraints=["Do not expose private keys in plaintext", "Ensure 60s token TTL"],
        council_id="arch-sec-council",
        council_session_id=session.session_id,
    )
    leaf = leaf_mgr.spawn_shadow(
        parent_bot_id="architect-bot",
        parent_bot_name="ArchitectBot",
        profile="default",
        task_description="Generate configuration for Envoy mTLS proxy with persistent tickets",
        custom_leaf_id="leaf-architect-exec-101",
        identity_snapshot=leaf_snapshot,
        run_in_background=False,
    )
    print(f"[+] Leaf execution recorded: {leaf.leaf_id} (status: {leaf.status})")

    # 7. Experience & Evolution
    proposals = evo_mgr.get_proposals()
    if not proposals:
        exp = evo_mgr.record_experience(
            bot_id="architect-bot",
            event_type="mission_completed",
            domain="architecture",
            summary="Session tickets reduce mTLS handshake latency to <0.8ms.",
            success=True,
            payload={"p99_latency_ms": 1.2, "handshake_savings_pct": 82.0},
        )
        proposal = evo_mgr.create_proposal(
            bot_id="architect-bot",
            base_version_hash=arch_ver.bundle_hash,
            risk_class=RISK_LOW,
            rationale="Incorporate validated session ticket caching into operational values.",
            proposed_values_patch="Always specify explicit session ticket caching when mTLS is enabled.",
            evidence_refs=[exp.id],
        )
        evo_mgr.update_status(proposal.id, STATUS_APPROVED, arch_ver.bundle_hash)
        print(f"[+] Evolution proposal recorded: {proposal.id} (status: APPROVED)")

    # 8. Shared Knowledge Triple
    known = civ_mgr.query_knowledge(subject="cluster.auth.standard")
    if not known:
        civ_mgr.record_assertion(
            subject="cluster.auth.standard",
            predicate="enforces",
            object_="mTLS-with-OAuth2-CRL-60s",
            confidence=0.98,
            provenance_ref=session.session_id,
        )
        print("[+] Civilizational knowledge assertion recorded.")

    print("\n[OK] HAOS Civilization bootstrap complete!")


if __name__ == "__main__":
    main()
