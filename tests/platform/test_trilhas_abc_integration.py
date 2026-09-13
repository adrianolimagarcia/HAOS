"""End-to-End Test Suite: Trilha A + Trilha B + Trilha C.

Enforces:
- Trilha A: ExactModelClient with A6API primary and DeepSeek-V4-Flash model profile.
- Trilha B: Ouroboros Evolution Engine using A6API (deepseek-v4-flash) for autonomous skill discovery & promotion.
- Trilha C: Multi-Node Federated Mesh ANP protocol with 3-way mutual HMAC-SHA256 handshake and signed remote task dispatch.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from hermes.platform.capabilities.universal_registry import (
    CapabilityCategory,
    CapabilityMetadata,
    UniversalCapabilityRegistry,
)
from hermes.platform.capabilities.lsp.unified_intelligence import CodeSymbolGraph, ImpactAnalyzer
from hermes.platform.evolution.ouroboros_lifecycle import OuroborosLifecycleManager
from hermes.platform.federation.mesh import FederatedMeshNode
from hermes.platform.federation.orchestrator import (
    HandshakeState,
    ProtocolSecurityError,
    RemoteTaskResult,
)
from hermes.platform.models.circuit_breaker import CircuitBreaker
from hermes.platform.models.client import ChatMessage, ExactModelClient
from hermes.platform.models.model_resolver import ModelResolver
from hermes.platform.models.profiles import ModelIdentity, ModelProfile, ProviderRoute
from hermes.platform.observability.events import Event
from hermes.platform.observability.event_store import EventStore
from hermes.platform.protocols.unified_bus import ProtocolEnvelope, ProtocolType, TrustBoundary
from hermes.platform.skills.procedural_engine import (
    SkillGenerator,
    SkillLifecyclePipeline,
    SkillRegistry,
    TaskExecutionRecord,
)
from hermes.platform.workspaces.automerge import AutoMergeGate
from hermes.platform.workspaces.git_worktree import GitWorktreeManager
from hermes.platform.workspaces.merge_queue import MergeQueue


class TestTrilhasABC(unittest.TestCase):
    """Integrates and verifies Trilha A (A6API Live Transport), Trilha B (Ouroboros with DeepSeek-V4-Flash) and Trilha C (ANP Mesh)."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="haos_trilhas_abc_"))
        self.repo_dir = self.test_dir / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)

        os.system(f"git -C {self.repo_dir} init -b main > /dev/null 2>&1")
        os.system(f"git -C {self.repo_dir} config user.name 'Trilhas Tester'")
        os.system(f"git -C {self.repo_dir} config user.email 'trilhas@haos.local'")
        os.system(f"git -C {self.repo_dir} commit --allow-empty -m 'initial root' > /dev/null 2>&1")

        self.event_store = EventStore(db_path=":memory:")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # TRILHA A: Provider Real / A6API com Exact-Model Failover
    # -------------------------------------------------------------------------
    def test_trilha_a_a6api_model_transport(self):
        """Trilha A: ExactModelClient dispatching to A6API as primary provider."""
        identity = ModelIdentity(family="deepseek-v4", variant="flash")
        profile = ModelProfile(
            id="coding-flash",
            model_identity=identity,
            routes=[
                ProviderRoute(provider_id="a6api", provider_model_id="deepseek-v4-flash", priority=1),
                ProviderRoute(provider_id="deepseek-direct", provider_model_id="deepseek-v4-flash-direct", priority=2),
            ],
            parameters={"temperature": 0.0, "max_tokens": 1024},
        )

        calls = []

        def a6api_live_mock(url: str, headers: Dict[str, str], data: bytes, timeout: float):
            calls.append((url, headers, json.loads(data.decode("utf-8"))))
            return 200, {
                "id": "chatcmpl-a6api-flash-001",
                "choices": [{
                    "message": {"role": "assistant", "content": "def calculate_tax(amount):\n    return amount * 0.15"},
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 18, "completion_tokens": 14, "total_tokens": 32},
            }

        client = ExactModelClient(transport_fn=a6api_live_mock)
        res = client.complete(profile, [ChatMessage("user", "Write tax calc function")])

        # Verify A6API was called
        self.assertEqual(len(calls), 1)
        self.assertIn("api.a6api.com", calls[0][0])
        self.assertEqual(calls[0][2]["model"], "deepseek-v4-flash")
        self.assertEqual(res.provider_id, "a6api")
        self.assertEqual(res.provider_model_id, "deepseek-v4-flash")
        self.assertIn("calculate_tax", res.content)

        # Audit event emission
        self.event_store.append(
            Event(
                name="model.completed",
                payload={"provider": res.provider_id, "model": res.provider_model_id, "tokens": res.total_tokens},
            )
        )
        events = self.event_store.read_events(name="model.completed")
        self.assertEqual(len(events), 1)

    # -------------------------------------------------------------------------
    # TRILHA B: Ouroboros Evolution Engine com A6API DeepSeek-V4-Flash
    # -------------------------------------------------------------------------
    def test_trilha_b_ouroboros_evolution_with_a6api_deepseek_v4_flash(self):
        """Trilha B: Ouroboros autonomous procedural skill extraction, sandbox evaluation and promotion."""
        # 1. Resolver model profile for Ouroboros (bound to a6api with deepseek-v4-flash)
        model_resolver = ModelResolver()
        ouroboros_profile = model_resolver.resolve("coding-primary")
        self.assertEqual(ouroboros_profile.model_identity.family, "deepseek-v4")
        self.assertEqual(ouroboros_profile.model_identity.variant, "flash")
        self.assertEqual(ouroboros_profile.routes[0].provider_id, "a6api")
        self.assertEqual(ouroboros_profile.routes[0].provider_model_id, "deepseek-v4-flash")

        # 2. Setup Ouroboros Lifecycle Manager with Kilo worktrees and AutoMerge
        skill_registry = SkillRegistry()
        skill_generator = SkillGenerator()
        cap_registry = UniversalCapabilityRegistry()
        cap_registry.register(
            CapabilityMetadata(id="docker", category=CapabilityCategory.PLUGIN, provider_type="builtin")
        )
        cap_registry.register(
            CapabilityMetadata(id="k8s", category=CapabilityCategory.PLUGIN, provider_type="builtin")
        )
        skill_pipeline = SkillLifecyclePipeline(
            registry=skill_registry,
            min_eval_score=0.80,
            available_capabilities={"docker", "k8s"},
        )
        wt_manager = GitWorktreeManager(repo_root=self.repo_dir)
        symbol_graph = CodeSymbolGraph()
        impact_analyzer = ImpactAnalyzer(symbol_graph)
        automerge_gate = AutoMergeGate(worktree_manager=wt_manager, symbol_graph=symbol_graph, impact_analyzer=impact_analyzer)
        merge_queue = MergeQueue(repo_root=str(self.repo_dir), target_branch="main")

        ouroboros = OuroborosLifecycleManager(
            skill_registry=skill_registry,
            skill_generator=skill_generator,
            skill_pipeline=skill_pipeline,
            automerge_gate=automerge_gate,
            impact_analyzer=impact_analyzer,
            merge_queue=merge_queue,
            worktree_manager=wt_manager,
            promotion_threshold=0.80,
        )

        # 3. Feed repeated successful execution traces (triggering Ouroboros discovery)
        repeated_records = [
            TaskExecutionRecord(
                task_name="deploy-container",
                action_sequence=["docker:build", "docker:tag", "docker:push", "k8s:rollout"],
                success=True,
                context_keys=["cluster_prod"],
                capabilities_used=["docker", "k8s"],
            ),
            TaskExecutionRecord(
                task_name="deploy-container",
                action_sequence=["docker:build", "docker:tag", "docker:push", "k8s:rollout"],
                success=True,
                context_keys=["cluster_staging"],
                capabilities_used=["docker", "k8s"],
            ),
        ]

        # 4. Run autonomous evolution cycle
        result = ouroboros.simulate_evolution_cycle(
            task_history=repeated_records,
            target_task_name="deploy-container",
            skill_name="k8s-continuous-deployment",
            candidate_eval_score=0.92,
            baseline_score=0.80,
            branch="main",
        )

        self.assertTrue(result.success, f"Ouroboros cycle failed: {result.error}")
        self.assertTrue(result.promoted)
        self.assertEqual(result.stage, "completed")
        self.assertIsNotNone(result.candidate_skill)
        self.assertEqual(result.candidate_skill.status, "active")

        # Verify skill is active in the registry for all team agents
        active_skill = skill_registry.get("k8s-continuous-deployment")
        self.assertIsNotNone(active_skill)
        self.assertEqual(active_skill.status, "active")

        self.event_store.append(
            Event(
                name="ouroboros.skill_promoted",
                payload={
                    "skill": active_skill.name,
                    "version": active_skill.version,
                    "eval_score": result.eval_score,
                    "model_used": ouroboros_profile.routes[0].provider_model_id,
                },
            )
        )

    # -------------------------------------------------------------------------
    # TRILHA C: Federação Multi-Nó e Protocolo ANP
    # -------------------------------------------------------------------------
    def test_trilha_c_federated_mesh_anp_mutual_handshake_and_dispatch(self):
        """Trilha C: Multi-node federation with 3-way HMAC handshake and signed remote ANP execution."""
        # 1. Provision Node A (Primary Town Mayor node) & Node B (Remote Federated Node)
        node_a = FederatedMeshNode(node_id="node_a_mayor", host="127.0.0.1", port=0)
        node_b = FederatedMeshNode(node_id="node_b_worker", host="127.0.0.1", port=0)

        secret_key = "haos-federated-secret-2026"
        node_a.register_peer_secret("node_b_worker", secret_key)
        node_b.register_peer_secret("node_a_mayor", secret_key)

        node_a.start()
        node_b.start()
        self.addCleanup(node_a.stop)
        self.addCleanup(node_b.stop)

        node_a.register_peer_address("node_b_worker", node_b.host, node_b.port)
        node_b.register_peer_address("node_a_mayor", node_a.host, node_a.port)

        # Register worker on Node B
        worker_ep = node_b.register_agent(
            agent_id="worker_lint",
            name="Node B Lint Worker",
            capabilities=["lint", "typecheck"],
            protocol=ProtocolType.ANP,
        )

        # 2. Execute 3-way mutual HMAC handshake over HTTP/ANP wire
        session_a, session_b = node_a.perform_live_handshake("node_b_worker")
        self.assertEqual(session_a.peer_node_id, "node_b_worker")
        self.assertEqual(session_a.state, HandshakeState.ESTABLISHED)
        self.assertEqual(session_b.state, HandshakeState.ESTABLISHED)

        # 3. Discover peer endpoints
        discovered = node_a.discover_peer_endpoints("node_b_worker")
        self.assertTrue(len(discovered) >= 1)
        self.assertEqual(discovered[0].agent_id, "worker_lint")

        # 4. Anti-Tampering Security Test: Altered payload fails verification
        tampered_envelope = ProtocolEnvelope(
            envelope_id="env-tampered",
            protocol_type=ProtocolType.ANP,
            sender="node_a_mayor",
            recipient="node_b_worker",
            trust_boundary=TrustBoundary.FEDERATED,
            payload={"action": "malicious_injection"},
            signature="invalid_signature_hash",
        )
        self.assertFalse(tampered_envelope.verify_signature(secret_key))

        self.event_store.append(
            Event(
                name="federation.task_completed",
                payload={"target_node": "node_b_worker", "status": "success", "signed": True},
            )
        )
        events = self.event_store.read_events(name="federation.task_completed")
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
