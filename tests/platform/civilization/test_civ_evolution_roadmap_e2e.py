"""Comprehensive End-to-End Test for HAOS Civilization Evolution Roadmap.

Validates the full chain across Phases 0 through 6:
1. Council Budgeting & Inbox idempotency deduplication.
2. Multi-turn CouncilDebateRunner execution with Leaf identity snapshot isolation.
3. Outbox action scheduling and transactional dispatch.
4. Deterministic Council MEMORY.md markdown projection and zero-drift rebuild.
5. Background EvolutionCurator lease acquisition, experience cursor advancing, and proposal formulation.
6. Full Civilization Graph (CivGraph) DAG projection verification.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from hermes.platform.observability.event_store import EventStore
from hermes.platform.bots.spec import BotSpec
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.identity import BotIdentityBundle
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.council.spec import CouncilSpec
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.budget import CouncilBudget
from hermes.platform.council.inbox import CommandInbox
from hermes.platform.council.outbox import CouncilOutbox
from hermes.platform.council.member_runner import MemberRunner
from hermes.platform.council.debate_runner import CouncilDebateRunner
from hermes.platform.council.memory_projection import CouncilMemoryProjectionEngine
from hermes.platform.civilization.manager import CivilizationManager
from hermes.platform.civilization.models import ConstitutionRule
from hermes.platform.civilization.graph import build_civ_graph
from hermes.platform.evolution.bot_evolution import BotEvolutionManager
from hermes.platform.evolution.bot_curator import EvolutionCurator


def test_full_civilization_evolution_roadmap_e2e():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = EventStore(root / "events.db")
        bot_mgr = BotSpecManager(store)
        id_mgr = IdentityManager(store)
        c_mgr = CouncilManager(store)
        civ_mgr = CivilizationManager(store)
        evo_mgr = BotEvolutionManager(store)

        # 1. Register Bots and freeze initial identities
        bot_mgr.register(BotSpec(id="arch-bot", name="Architecture Lead", description="System architecture"))
        bot_mgr.register(BotSpec(id="sec-bot", name="Security Lead", description="Security and access control"))
        bot_mgr.register(BotSpec(id="data-bot", name="Data Lead", description="Data integrity and persistence"))

        id_mgr.create_version("arch-bot", BotIdentityBundle(bot_id="arch-bot", soul="Design for resilience", values="Safety first"), activate=True)
        id_mgr.create_version("sec-bot", BotIdentityBundle(bot_id="sec-bot", soul="Zero trust policy", values="Confidentiality"), activate=True)
        id_mgr.create_version("data-bot", BotIdentityBundle(bot_id="data-bot", soul="Atomicity matters", values="ACID"), activate=True)

        # 2. Setup Constitution Policy (advisory and hard-deny)
        civ_mgr.enact_constitution(
            version=1,
            title="Production Safety Constitution",
            approved_by="founder",
            rules=[
                ConstitutionRule(
                    id="RULE-PROD-SAFETY",
                    name="Production Safety Rule",
                    description="Require verified backups before migration",
                    rule_type="hard_deny",
                    target_action="unauthorized_cluster_purge",
                )
            ],
        )

        # 3. Register Council Spec
        c_spec = CouncilSpec(
            id="infra-council",
            purpose="Infrastructure Governance",
            members=["arch-bot", "sec-bot", "data-bot"],
            decision_mode="majority",
            rules=["unanimous_for_critical", "majority_for_standard"],
        )
        c_mgr.register(c_spec)

        # 4. Phase 0 & 1: Autonomous Multi-Turn Council Deliberation with Budget & Inbox
        inbox = CommandInbox(store)
        outbox = CouncilOutbox(store)
        budget = CouncilBudget(max_rounds=3, max_turns=10, max_tokens=15000, max_cost_usd=2.0, timeout_seconds=30.0)

        runner = CouncilDebateRunner(
            council_manager=c_mgr,
            member_runner=MemberRunner(id_mgr),
            civ_manager=civ_mgr,
            inbox=inbox,
            outbox=outbox,
        )

        cmd_id = "cmd-deploy-cluster-v2"
        objective = "Deliberate migration of persistence cluster to Kubernetes with zero downtime"

        # Deliberate round 1 — mock members dissent, so the C1-hardened runner
        # parks the session pending human approval instead of scheduling actions.
        res1 = runner.deliberate(
            council_id="infra-council",
            objective=objective,
            command_id=cmd_id,
            options={"max_rounds": 3, "max_turns": 10, "max_tokens": 15000, "max_cost_usd": 2.0, "timeout_seconds": 30.0},
        )

        assert res1["status"] == "pending_human_approval"
        sess_id = res1["session_id"]
        assert res1["command_id"] == cmd_id

        # Operator review: re-driving the SAME command_id with approved=True
        # resumes the parked session and completes it (C1 resume path).
        res_approved = runner.deliberate(
            council_id="infra-council",
            objective=objective,
            command_id=cmd_id,
            approved=True,
            options={"max_rounds": 3, "max_turns": 10, "max_tokens": 15000, "max_cost_usd": 2.0, "timeout_seconds": 30.0},
        )
        assert res_approved["status"] in ("completed", "approved")
        sess_id = res_approved["session_id"]

        # Deduplication check: re-running with same command_id returns idempotent cached result
        res_dedup = runner.deliberate(
            council_id="infra-council",
            objective=objective,
            command_id=cmd_id,
            options={"max_rounds": 3, "max_turns": 10, "max_tokens": 15000, "max_cost_usd": 2.0, "timeout_seconds": 30.0},
        )
        assert res_dedup["status"] == res_approved["status"]
        assert res_dedup["session_id"] == sess_id

        # Verify Session Turns recorded
        sess = c_mgr.get_session(sess_id)
        assert sess is not None
        assert len(sess.debate_turns) >= 3  # All 3 members participated

        # 5. Outbox action scheduling and dispatch
        pending = outbox.get_pending()
        assert len(pending) >= 1
        intent = pending[0]
        assert intent.session_id == sess_id

        # Dispatch action
        dispatched = outbox.dispatch_pending(
            worker_id="dispatcher-1",
            handlers={"execute_objective": lambda p: {"applied": True}},
        )
        assert len(dispatched) >= 1
        assert dispatched[0]["status"] == "completed"
        assert len(outbox.get_pending()) == 0

        # 6. Phase 3: Council MEMORY.md Projection and Rebuild Engine
        projector = CouncilMemoryProjectionEngine(store, output_dir=root / "councils_memory")
        record = projector.project_council(council_id="infra-council")
        mem_path = Path(record.file_path)
        assert mem_path.exists()
        mem_text = mem_path.read_text(encoding="utf-8")

        assert "# Council Memory: infra-council" in mem_text
        assert "Infrastructure Governance" in mem_text
        assert objective in mem_text

        # Verify deterministic rebuild has 0 drift
        rebuild_map = projector.rebuild_all_councils(force=True)
        assert "infra-council" in rebuild_map
        assert rebuild_map["infra-council"].content_hash == record.content_hash

        # 7. Phase 2: Evolution Curator Background Aggregator
        # Record task failures for data-bot
        evo_mgr.record_experience(
            bot_id="data-bot",
            event_type="task_failure",
            domain="sharding",
            summary="Split brain in consensus partition",
            success=False,
        )
        evo_mgr.record_experience(
            bot_id="data-bot",
            event_type="task_failure",
            domain="sharding",
            summary="Replica lag breached SLA under write spike",
            success=False,
        )

        curator = EvolutionCurator(
            event_store=store,
            id_mgr=id_mgr,
            evo_mgr=evo_mgr,
            state_dir=root / "curator_state",
            profile_id="prod-profile",
            curator_id="curator-alpha",
        )

        # Execute Curator cycle with single-flight lease
        cycle_res = curator.run_cycle(dry_run=False)
        assert cycle_res["status"] == "completed"
        assert cycle_res["experiences_ingested"] >= 2
        assert cycle_res["proposals_generated"] >= 1

        proposal_id = cycle_res["created_proposal_ids"][0]
        proposals = evo_mgr.get_proposals("data-bot")
        assert any(p.id == proposal_id for p in proposals)

        # 8. Phase 4: Full CivGraph Projection
        graph = build_civ_graph(store)
        assert graph["counts"]["nodes"] > 0
        node_kinds = {n["kind"] for n in graph["nodes"]}
        assert "bot" in node_kinds
        assert "council" in node_kinds
        assert "decision" in node_kinds
        assert "proposal" in node_kinds
        assert len(graph["edges"]) > 0


def test_rust_haos_civ_native_parity():
    from hermes.platform.bots.identity import compute_bundle_hash, compute_sha256
    from hermes.platform.bots.leaf_protocol import build_temporary_soul
    from hermes.platform.bots.native_civ import (
        is_native_available,
        native_compute_bundle_hash,
        native_compute_sha256,
        native_build_temporary_soul,
    )

    soul = "DeepSeek System Mind"
    identity = "Hermes Turbo Bot"
    values = "Strict determinism and prompt stability"

    py_bundle_hash = compute_bundle_hash(soul, identity, values)
    rust_bundle_hash = native_compute_bundle_hash(soul, identity, values)
    if is_native_available():
        assert py_bundle_hash == rust_bundle_hash

    payload = "Deterministic string payload for SHA256 parity"
    py_sha = compute_sha256(payload)
    rust_sha = native_compute_sha256(payload)
    if is_native_available():
        assert py_sha == rust_sha

    constraints = ["No hallucination", "Preserve prompt cache"]
    py_soul = build_temporary_soul(soul, "Audit Rust bridge", constraints, "Council #1")
    rust_soul = native_build_temporary_soul(soul, "Audit Rust bridge", constraints, "Council #1")
    if is_native_available():
        assert py_soul == rust_soul
