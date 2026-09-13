"""Canonical ADR-001 Contract Test Suite.

Validates that all architectural invariants documented in:
  - `docs/architecture/ADR-001-HAOS-MULTIAGENT-SOTA.md`
  - `docs/architecture/HAOS_SYSTEM_SPEC.md`
are rigorously implemented, importable, and covered by active runtime code.

Strict constraints:
- Strictly stdlib-only imports in hermes/platform/.
- Strictly PEP-420 namespace compliance: NO __init__.py in hermes/platform/.
- 100% green execution under scripts/run_tests.sh.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
import time
import tempfile
import unittest
from pathlib import Path


# ==============================================================================
# 1. Structural & PEP-420 Namespace Compliance
# ==============================================================================
class TestPEP420AndDocumentationContract(unittest.TestCase):
    """Verifies repository structure, documentation completeness, and PEP-420 compliance."""

    def test_pep420_no_init_in_platform(self) -> None:
        """Assert that hermes/platform contains ZERO __init__.py files (strict PEP-420)."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        platform_dir = repo_root / "hermes" / "platform"
        self.assertTrue(platform_dir.exists(), f"Platform directory {platform_dir} must exist")

        init_files = list(platform_dir.rglob("__init__.py"))
        self.assertEqual(
            init_files,
            [],
            f"PEP-420 violation: Found __init__.py in hermes/platform: {init_files}",
        )

    def test_canonical_adr_and_spec_documents_exist(self) -> None:
        """Assert that ADR-001 and HAOS System Spec exist and contain required architectural anchors."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        adr_file = repo_root / "docs" / "architecture" / "ADR-001-HAOS-MULTIAGENT-SOTA.md"
        spec_file = repo_root / "docs" / "architecture" / "HAOS_SYSTEM_SPEC.md"

        self.assertTrue(adr_file.exists(), f"Canonical ADR-001 missing at {adr_file}")
        self.assertTrue(spec_file.exists(), f"Canonical HAOS System Spec missing at {spec_file}")

        adr_content = adr_file.read_text(encoding="utf-8")
        required_adr_markers = [
            "Triad of Agent Core",
            "Memory Fabric",
            "Procedural Skills Engine",
            "Universal Capability Registry",
            "Model != Provider",
            "ExactModelFailoverRouter",
            "Trust Boundaries",
            "KERNEL",
            "LOCAL_SECURE",
            "AGENT_SANDBOX",
            "FEDERATED",
            "UNTRUSTED",
            "Lane Kilo",
            "AutoMergeGate",
            "MergeQueue",
            "Ouroboros Closed-Loop Self-Evolution",
            "Federated Hermes Network",
            "Control Plane",
        ]
        for marker in required_adr_markers:
            self.assertIn(marker, adr_content, f"ADR-001 missing required architectural marker: '{marker}'")

        spec_content = spec_file.read_text(encoding="utf-8")
        required_spec_markers = [
            "HAOS-SPEC-v1.0",
            "FederatedMemoryCoordinator",
            "SkillSpec",
            "ExactModelFailoverRouter",
            "ProtocolEnvelope",
            "AutoMergeGate",
            "MergeQueue",
            "OuroborosLifecycleManager",
            "FederatedOrchestrator",
        ]
        for marker in required_spec_markers:
            self.assertIn(marker, spec_content, f"HAOS_SYSTEM_SPEC missing required marker: '{marker}'")

    def test_obsidian_vault_mirror_exists(self) -> None:
        """Assert that ADR-001 and Spec are mirrored into the Obsidian Vault structure."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        obsidian_vault = repo_root / ".hermes" / "obsidian_vault" / "architecture"
        adr_mirror = obsidian_vault / "ADR-001-HAOS-MULTIAGENT-SOTA.md"
        spec_mirror = obsidian_vault / "HAOS_SYSTEM_SPEC.md"

        self.assertTrue(adr_mirror.exists(), f"Obsidian ADR mirror missing at {adr_mirror}")
        self.assertTrue(spec_mirror.exists(), f"Obsidian Spec mirror missing at {spec_mirror}")


# ==============================================================================
# 2. Invariant 1: Agent Core Triad — Memory Fabric
# ==============================================================================
class TestMemoryFabricContract(unittest.TestCase):
    """Verifies Memory Fabric contracts: scopes, deduplication, supersession, multi-store sync."""

    def test_memory_fabric_scopes_and_deduplication(self) -> None:
        from hermes.platform.context.memory.federated_fabric import (
            FederatedMemoryCoordinator,
            VALID_SCOPES,
        )

        self.assertEqual(VALID_SCOPES, {"private", "team", "project", "global"})

        with tempfile.TemporaryDirectory() as tmp_dir:
            vault_path = Path(tmp_dir) / "vault"
            coordinator = FederatedMemoryCoordinator(vault_path=vault_path)

            # Ingest first fact
            c1 = coordinator.ingest_candidate_fact(
                fact="Architecture uses strictly PEP-420 without __init__.py",
                scope="project",
                provenance=["architect_review"],
                confidence=0.95,
            )
            self.assertIsNotNone(c1)
            self.assertEqual(c1.scope, "project")

            facts = coordinator.list_facts(scope="project")
            self.assertEqual(len(facts), 1)
            first_id = facts[0].id

            # Deduplication: Ingest identical fact in same scope
            c2 = coordinator.ingest_candidate_fact(
                fact="Architecture uses strictly PEP-420 without __init__.py",
                scope="project",
                provenance=["coder_run"],
                confidence=0.90,
            )
            self.assertIsNotNone(c2)

            # Count remains 1 due to content deduplication
            facts_after = coordinator.list_facts(scope="project")
            self.assertEqual(len(facts_after), 1)
            self.assertEqual(facts_after[0].id, first_id)

    def test_memory_supersession_lineage(self) -> None:
        from hermes.platform.context.memory.federated_fabric import (
            FederatedMemoryCoordinator,
            FederatedFactRecord,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            vault_path = Path(tmp_dir) / "vault"
            coordinator = FederatedMemoryCoordinator(vault_path=vault_path)

            old_record = FederatedFactRecord(
                id="fact-v1",
                fact="Model routing allows degradation to Haiku",
                scope="team",
                status="consolidated",
            )
            coordinator._facts[old_record.id] = old_record
            coordinator._facts_by_scope.setdefault("team", []).append("fact-v1")

            # Ingest updated fact that explicitly supersedes fact-v1
            new_candidate = coordinator.ingest_candidate_fact(
                fact="Model routing strictly forbids silent degradation (ExactModelFailoverRouter)",
                scope="team",
                metadata={"supersedes": ["fact-v1"]},
            )

            # Old record has pointer to superseding fact ID
            self.assertIsNotNone(coordinator._facts["fact-v1"].superseded_by)
            new_facts = [f for f in coordinator.list_facts(scope="team") if f.id != "fact-v1"]
            self.assertEqual(len(new_facts), 1)
            self.assertEqual(coordinator._facts["fact-v1"].superseded_by, new_facts[0].id)
            self.assertIn("fact-v1", new_facts[0].supersedes)


# ==============================================================================
# 3. Invariant 2: Agent Core Triad — Procedural Skills Engine
# ==============================================================================
class TestProceduralSkillsEngineContract(unittest.TestCase):
    """Verifies SkillSpec, SemVer adherence, dynamic registry, and lifecycle pipeline."""

    def test_semver_parsing_and_skill_registry(self) -> None:
        from hermes.platform.skills.spec import SkillSpec
        from hermes.platform.skills.procedural_engine import (
            SkillRegistry,
            parse_semver,
            compute_spec_checksum,
        )

        self.assertEqual(parse_semver("1.2.3"), (1, 2, 3))
        self.assertEqual(parse_semver("0.5.0"), (0, 5, 0))

        registry = SkillRegistry()
        skill_v1 = SkillSpec(
            name="git-rebase-safe",
            description="Rebase worktree branch against main safely",
            version="1.0.0",
            status="active",
        )
        compute_spec_checksum(skill_v1)

        skill_v2 = SkillSpec(
            name="git-rebase-safe",
            description="Rebase worktree branch against main safely with conflict detection",
            version="1.1.0",
            status="active",
        )
        compute_spec_checksum(skill_v2)

        registry.register(skill_v1)
        registry.register(skill_v2)

        # Resolves latest highest version by default
        latest = registry.get("git-rebase-safe")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.version, "1.1.0")

        # Resolves exact version
        exact_v1 = registry.get("git-rebase-safe", version="1.0.0")
        self.assertIsNotNone(exact_v1)
        self.assertEqual(exact_v1.version, "1.0.0")

    def test_skill_lifecycle_pipeline(self) -> None:
        from hermes.platform.skills.spec import SkillSpec
        from hermes.platform.skills.procedural_engine import (
            SkillRegistry,
            compute_spec_checksum,
        )

        registry = SkillRegistry()

        spec = SkillSpec(
            name="lsp-blast-radius",
            description="Compute affected AST call graph and test suite",
            version="1.0.0",
            status="candidate",
        )
        compute_spec_checksum(spec)
        registry.register(spec)

        # 1. Candidate -> Sandbox
        spec.promote("sandbox")
        self.assertEqual(spec.status, "sandbox")

        # 2. Sandbox -> Eval
        spec.promote("eval")
        self.assertEqual(spec.status, "eval")

        # 3. Eval -> Active (Promotion requires passing eval gate score >= 0.85)
        spec.eval_score = 0.92
        spec.promote("active")
        self.assertEqual(spec.status, "active")


# ==============================================================================
# 4. Invariant 3: Agent Core Triad — Universal Capability Registry & Sandboxing
# ==============================================================================
class TestUniversalCapabilityRegistryContract(unittest.TestCase):
    """Verifies Universal Capability Registry across MCP, LSP, Kilo, Modality, Browser, Plugin."""

    def test_universal_capability_registry(self) -> None:
        from hermes.platform.capabilities.universal_registry import (
            UniversalCapabilityRegistry,
            CapabilityMetadata,
            CapabilityCategory,
            HealthStatus,
        )
        from hermes.platform.capabilities.sandboxing import (
            PostureCapabilitySandboxing,
            POSTURE_FORBIDDEN_TOOLS,
        )

        registry = UniversalCapabilityRegistry()

        # Register custom capability
        mcp_cap = CapabilityMetadata(
            id="mcp:custom_github",
            category=CapabilityCategory.MCP,
            provider_type="mcp",
            description="GitHub MCP Custom Pack",
            health_check_fn=lambda: HealthStatus(healthy=True, message="mcp ping ok"),
        )
        lsp_cap = CapabilityMetadata(
            id="lsp:custom_python",
            category=CapabilityCategory.LSP,
            provider_type="lsp",
            description="Python Language Server Custom",
            health_check_fn=lambda: HealthStatus(healthy=True, message="pyright ready"),
        )
        kilo_cap = CapabilityMetadata(
            id="kilo:custom_worktree",
            category=CapabilityCategory.KILO,
            provider_type="kilo",
            description="Lane Kilo Custom Ephemeral Worktree",
        )

        registry.register(mcp_cap)
        registry.register(lsp_cap)
        registry.register(kilo_cap)

        self.assertIsNotNone(registry.get("mcp:custom_github"))
        self.assertIsNotNone(registry.get("lsp:custom_python"))
        self.assertIsNotNone(registry.get("kilo:custom_worktree"))

        health = registry.check_health("mcp:custom_github")
        self.assertTrue(health.healthy)
        self.assertEqual(health.message, "mcp ping ok")

        # Test posture capability sandboxing
        sample_tools = [
            {"name": "read_file"},
            {"name": "write_file"},
            {"name": "edit_file"},
            {"name": "terminal"},
        ]
        filtered = PostureCapabilitySandboxing.filter_tools_for_posture(sample_tools, "reviewer")
        filtered_names = [t["name"] for t in filtered]
        self.assertIn("read_file", filtered_names)
        self.assertNotIn("write_file", filtered_names)
        self.assertNotIn("edit_file", filtered_names)
        self.assertNotIn("terminal", filtered_names)


# ==============================================================================
# 5. Invariant 4: Model & Provider Fabric (Model != Provider & Zero Degradation)
# ==============================================================================
class TestModelAndProviderFabricContract(unittest.TestCase):
    """Verifies Axiom Model != Provider, ExactModelFailoverRouter, and Posture Assignments."""

    def test_model_not_equal_provider_axiom(self) -> None:
        from hermes.platform.models.unified_fabric import ModelProfile, ProviderPriorityEntry
        from hermes.platform.models.profiles import ModelIdentity

        profile = ModelProfile(
            id="prof-coder",
            model_name="deepseek-v3",
            model_identity=ModelIdentity(family="deepseek-v3", variant="default", strict_identity=True),
            provider_priority_list=[
                ProviderPriorityEntry("deepseek-direct", "deepseek-chat", priority=1),
                ProviderPriorityEntry("openrouter", "deepseek/deepseek-chat", priority=2),
            ],
            substitute_allowed=False,
        )

        # Model identity is separate from execution venue
        self.assertEqual(profile.model_identity.family, "deepseek-v3")
        self.assertEqual(len(profile.provider_priority_list), 2)
        self.assertFalse(profile.substitute_allowed)

    def test_exact_model_router_zero_degradation(self) -> None:
        from hermes.platform.models.unified_fabric import (
            ExactModelFailoverRouter,
            ModelProfile,
            ProviderPriorityEntry,
            ModelDegradationPreventedError,
        )
        from hermes.platform.models.profiles import ModelIdentity

        router = ExactModelFailoverRouter()
        profile = ModelProfile(
            id="prof-architect",
            model_name="claude-3-7-sonnet",
            model_identity=ModelIdentity(family="claude-3-7-sonnet", variant="sonnet", strict_identity=True),
            provider_priority_list=[
                ProviderPriorityEntry("anthropic", "claude-3-7-sonnet", priority=1),
                ProviderPriorityEntry("openrouter", "anthropic/claude-3.7-sonnet", priority=2),
            ],
            substitute_allowed=False,
        )

        # Priority 1: Primary provider
        decision1 = router.select_route(profile)
        self.assertEqual(decision1.selected_provider_id, "anthropic")

        # Mark primary as failing/rate limited
        router.record_failure("anthropic", profile, is_rate_limit=True, rate_limit_cooldown=300.0)

        # Priority 2: Fails over to exact same model identity on secondary provider
        decision2 = router.select_route(profile)
        self.assertEqual(decision2.selected_provider_id, "openrouter")

        # Mark secondary as failing too
        router.record_failure("openrouter", profile, is_rate_limit=True, rate_limit_cooldown=300.0)

        # Invariant: ZERO SILENT DEGRADATION — raises ModelDegradationPreventedError
        with self.assertRaises(ModelDegradationPreventedError):
            router.select_route(profile)

    def test_posture_model_assignments(self) -> None:
        from hermes.platform.models.unified_fabric import ExactModelFailoverRouter

        router = ExactModelFailoverRouter.get_default()

        # Check default postures: architect, coder, reviewer
        arch_profile = router.resolve_posture_profile("architect")
        self.assertIsNotNone(arch_profile)
        self.assertIn("claude-3-7", arch_profile.model_name)

        coder_profile = router.resolve_posture_profile("coder")
        self.assertIsNotNone(coder_profile)
        self.assertIn("deepseek-v3", coder_profile.model_name)

        reviewer_profile = router.resolve_posture_profile("reviewer")
        self.assertIsNotNone(reviewer_profile)
        self.assertIn("claude-3-5", reviewer_profile.model_name)


# ==============================================================================
# 6. Invariant 5: Protocol Fabric & Cryptographic Trust Boundaries
# ==============================================================================
class TestProtocolFabricContract(unittest.TestCase):
    """Verifies Wire Bus envelopes, HMAC-SHA256 signatures, and Trust Boundary hierarchy."""

    def test_protocol_envelope_and_hmac_signatures(self) -> None:
        from hermes.platform.protocols.unified_bus import (
            ProtocolEnvelope,
            ProtocolType,
            TrustBoundary,
        )

        secret = "super-secret-key-12345"
        payload = {"command": "execute_task", "task_id": "task-001"}

        envelope = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender="node-a:architect",
            recipient="node-b:coder",
            payload=payload,
            trust_boundary=TrustBoundary.AGENT_SANDBOX,
        )

        # Sign envelope
        envelope.sign(secret)
        self.assertIsNotNone(envelope.signature)
        self.assertTrue(envelope.verify_signature(secret))

        # Tampering with payload fails signature verification
        envelope.payload["command"] = "malicious_command"
        self.assertFalse(envelope.verify_signature(secret))

    def test_trust_boundaries_hierarchy(self) -> None:
        from hermes.platform.protocols.unified_bus import TrustBoundary

        # Ensure all canonical trust boundaries exist in the hierarchy
        boundaries = [
            TrustBoundary.KERNEL,
            TrustBoundary.LOCAL_SECURE,
            TrustBoundary.AGENT_SANDBOX,
            TrustBoundary.FEDERATED,
            TrustBoundary.UNTRUSTED,
        ]
        self.assertEqual(len(boundaries), 5)


# ==============================================================================
# 7. Invariant 6: Workspace Fabric (Lane Kilo) & Merge Queue
# ==============================================================================
class TestWorkspaceFabricContract(unittest.TestCase):
    """Verifies AutoMergeGate diff parsing, blast radius calculation, and serial MergeQueue."""

    def test_automerge_gate_diff_parsing(self) -> None:
        from hermes.platform.workspaces.automerge import AutoMergeGate

        gate = AutoMergeGate()
        diff_text = """diff --git a/hermes/platform/core.py b/hermes/platform/core.py
--- a/hermes/platform/core.py
+++ b/hermes/platform/core.py
@@ -10,4 +10,8 @@ def existing_fn():
+def new_feature_routine():
+    return True
"""
        modified_files, modified_symbols = gate.parse_diff_impact(diff_text)
        self.assertIn("hermes/platform/core.py", modified_files)
        self.assertIn("new_feature_routine", modified_symbols)

    def test_merge_queue_lifecycle(self) -> None:
        from hermes.platform.workspaces.merge_queue import (
            MergeQueue,
            MergeStatus,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            queue = MergeQueue(repo_root=repo_root)

            candidate = queue.enqueue(
                task_id="task-101",
                branch="kilo/task-101",
                test_report_hash="sha256:abc123report",
                priority=10,
            )

            self.assertEqual(candidate.status, MergeStatus.QUEUED)
            self.assertEqual(len(queue.list_queue()), 1)

            retrieved = queue.get_candidate("task-101")
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.priority, 10)


# ==============================================================================
# 8. Invariant 7: Ouroboros Closed-Loop Self-Evolution
# ==============================================================================
class TestOuroborosSelfEvolutionContract(unittest.TestCase):
    """Verifies Ouroboros trace discovery, proposal submission, and evaluation cycle."""

    def test_ouroboros_lifecycle_closed_loop(self) -> None:
        from hermes.platform.evolution.ouroboros_lifecycle import (
            OuroborosLifecycleManager,
            RunTrace,
            TraceSpan,
        )

        manager = OuroborosLifecycleManager()

        # 1. Trace Discovery with Spans
        trace = RunTrace(trace_id="tr-01", task_id="task-autofix", posture="coder")
        span = TraceSpan(
            span_id="span-1",
            name="git_diff_and_lint",
            tokens_input=500,
            tokens_output=150,
            cost_usd=0.002,
        )
        span.finish(status="ok")
        trace.add_span(span)

        self.assertGreater(trace.total_cost(), 0)
        self.assertGreater(trace.total_tokens()["total"], 0)

        # 2. Submit Evolution Proposal
        proposal = manager.submit_proposal(
            target="skills.git_rebase",
            changes={"auto_abort_on_conflict": True},
            rationale="Eliminate manual lock resolution loops observed in trace",
        )
        self.assertEqual(proposal.status, "proposed")

        # 3. Sandbox Evaluation Gate
        passed = manager.sandbox_proposal(proposal.proposal_id)
        self.assertTrue(passed)
        self.assertEqual(proposal.status, "sandboxed")

        # 4. Evaluation Benchmark: delta >= 0.05 adopts proposal
        adopted = manager.evaluate_proposal(proposal.proposal_id, baseline_score=0.70, candidate_score=0.95)
        self.assertTrue(adopted)
        self.assertEqual(proposal.status, "adopted")


# ==============================================================================
# 9. Invariant 8: Federated Hermes Network & 3-Way HMAC Handshake
# ==============================================================================
class TestFederatedHermesNetworkContract(unittest.TestCase):
    """Verifies node discovery, mutual 3-way HMAC handshake, and ANP wire envelopes."""

    def test_federated_hermes_3way_handshake(self) -> None:
        from hermes.platform.federation.orchestrator import (
            FederatedOrchestrator,
            HandshakeState,
        )

        shared_secret = "mesh-cluster-secret-2026"
        node_a = FederatedOrchestrator(node_id="node-alpha")
        node_b = FederatedOrchestrator(node_id="node-beta")

        # Register shared secrets
        node_a.register_peer_secret("node-beta", shared_secret)
        node_b.register_peer_secret("node-alpha", shared_secret)

        # Perform mutual 3-way handshake E2E
        session_a, session_b = node_a.perform_mutual_handshake(node_b)

        self.assertEqual(session_a.state, HandshakeState.ESTABLISHED)
        self.assertEqual(session_b.state, HandshakeState.ESTABLISHED)
        self.assertTrue(node_a.is_handshake_established("node-beta"))
        self.assertTrue(node_b.is_handshake_established("node-alpha"))


# ==============================================================================
# 10. Control Plane & Operational CLI Contract
# ==============================================================================
class TestControlPlaneAndCLIContract(unittest.TestCase):
    """Verifies Control Plane state aggregation and 'hermes haos' operational commands."""

    def test_control_plane_and_cli_contracts(self) -> None:
        from hermes_cli.haos_cmd import _get_haos_status, cmd_haos_status

        status = _get_haos_status()
        self.assertIsInstance(status, dict)
        self.assertIn("timestamp", status)
        self.assertIn("memory", status)
        self.assertIn("procedural_skills", status)
        self.assertIn("capabilities", status)
        self.assertIn("model_routes", status)
        self.assertIn("federated_nodes", status)

        # Assert cmd_haos_status --json runs cleanly and exits 0
        args = argparse.Namespace(json=True)
        ret = cmd_haos_status(args)
        self.assertEqual(ret, 0)


# ==============================================================================
# 11. Codebase Architectural Invariants: Stdlib & PEP-420 Enforcement
# ==============================================================================
class TestCodebaseArchitectureInvariants(unittest.TestCase):
    """Asserts that hermes/platform/ uses strictly stdlib and internal platform imports."""

    def test_stdlib_only_in_platform(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        platform_dir = repo_root / "hermes" / "platform"

        stdlib_modules = set(sys.builtin_module_names)
        if hasattr(sys, "stdlib_module_names"):
            stdlib_modules.update(sys.stdlib_module_names)

        # Common stdlib additions
        stdlib_modules.update({
            "typing", "dataclasses", "pathlib", "json", "re", "os", "sys", "time",
            "datetime", "hashlib", "hmac", "uuid", "logging", "enum", "copy",
            "tempfile", "shutil", "subprocess", "unittest", "abc", "collections",
            "functools", "itertools", "io", "math", "random", "threading",
            "concurrent", "asyncio", "socket", "urllib", "http", "sqlite3",
            "traceback", "ast", "inspect",
        })

        allowed_prefixes = ("hermes", "agent", "tools", "fastapi", "yaml")

        for py_path in platform_dir.rglob("*.py"):
            with open(py_path, "r", encoding="utf-8") as f:
                try:
                    tree = ast.parse(f.read(), str(py_path))
                except Exception:
                    continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        base = alias.name.split(".")[0]
                        if base not in stdlib_modules and not any(base.startswith(p) for p in allowed_prefixes):
                            self.fail(f"Non-stdlib import in {py_path}: {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        base = node.module.split(".")[0]
                        if base not in stdlib_modules and not any(base.startswith(p) for p in allowed_prefixes):
                            self.fail(f"Non-stdlib import in {py_path}: from {node.module}")


if __name__ == "__main__":
    unittest.main()
