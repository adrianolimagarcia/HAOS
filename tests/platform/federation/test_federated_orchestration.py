"""Tests for Federated Hermes Handshake & Discovery E2E — Passo 2.

Comprehensive test suite verifying:
1. Peer discovery via FederatedAgentDirectory.
2. Mutual signed cryptographic handshake (HMAC-SHA256 nonces + signature exchange).
3. End-to-end remote task dispatch with HMAC-signed response verification
   and PerceptionArtifact recovery across node boundaries.
Strict stdlib-only; PEP-420 namespace compliant (no __init__.py).
"""

import asyncio
import hashlib
import hmac
import time
import unittest
from unittest.mock import MagicMock

from hermes.platform.capabilities.modality.workers import PerceptionArtifact
from hermes.platform.federation.orchestrator import (
    FederatedOrchestrator,
    HandshakeRole,
    HandshakeSession,
    HandshakeState,
    RemoteTaskResult,
)
from hermes.platform.protocols.unified_bus import (
    CrossProtocolBridge,
    FederatedAgentDirectory,
    FederatedAgentEndpoint,
    ProtocolEnvelope,
    ProtocolRouter,
    ProtocolSecurityError,
    ProtocolType,
    TrustBoundary,
)


class TestFederatedPeerDiscovery(unittest.TestCase):
    """Test discovery of peer agents across nodes via FederatedAgentDirectory."""

    def setUp(self):
        self.orchestrator = FederatedOrchestrator(
            node_id="node-alpha",
            secret_key="secret-alpha",
        )

    def test_register_and_discover_peer_agents(self):
        # Register secrets
        self.orchestrator.register_peer_secret("node-beta", "secret-beta")

        # Register remote agents on node-beta
        ep1 = self.orchestrator.register_peer_agent(
            agent_id="agent-vision-1",
            name="Vision Specialist",
            node_id="node-beta",
            protocol=ProtocolType.ANP,
            trust_boundary=TrustBoundary.FEDERATED,
            capabilities=["vision", "multimodal_analysis"],
        )
        ep2 = self.orchestrator.register_peer_agent(
            agent_id="agent-coder-1",
            name="Code Implementer",
            node_id="node-beta",
            protocol=ProtocolType.A2A,
            trust_boundary=TrustBoundary.FEDERATED,
            capabilities=["coding", "git"],
        )

        # Lookup by agent_id
        found = self.orchestrator.get_agent_endpoint("agent-vision-1")
        self.assertIsNotNone(found)
        self.assertEqual(found.agent_id, "agent-vision-1")
        self.assertEqual(found.metadata["node_id"], "node-beta")

        # Discovery by capability
        vision_agents = self.orchestrator.discover_peer_agents(capability="vision")
        self.assertEqual(len(vision_agents), 1)
        self.assertEqual(vision_agents[0].agent_id, "agent-vision-1")

        # Discovery by protocol
        anp_agents = self.orchestrator.discover_peer_agents(protocol=ProtocolType.ANP)
        self.assertEqual(len(anp_agents), 1)
        self.assertEqual(anp_agents[0].agent_id, "agent-vision-1")

        # Discovery by node_id
        beta_agents = self.orchestrator.discover_peer_agents(node_id="node-beta")
        self.assertEqual(len(beta_agents), 2)
        agent_ids = {a.agent_id for a in beta_agents}
        self.assertEqual(agent_ids, {"agent-vision-1", "agent-coder-1"})

    def test_discover_nonexistent_capability(self):
        results = self.orchestrator.discover_peer_agents(capability="quantum_computing")
        self.assertEqual(results, [])


class TestMutualCryptographicHandshake(unittest.TestCase):
    """Test mutual signed cryptographic handshake between nodes."""

    def setUp(self):
        self.node_alpha = FederatedOrchestrator(
            node_id="node-alpha",
            secret_key="shared-secret-ab",
        )
        self.node_beta = FederatedOrchestrator(
            node_id="node-beta",
            secret_key="shared-secret-ab",
        )

        # Pre-shared secret setup
        self.node_alpha.register_peer_secret("node-beta", "shared-secret-ab")
        self.node_beta.register_peer_secret("node-alpha", "shared-secret-ab")

    def test_step_by_step_handshake_success(self):
        # Step 1: Alpha initiates
        init_envelope = self.node_alpha.initiate_handshake("node-beta")
        self.assertEqual(init_envelope.sender, "node-alpha")
        self.assertEqual(init_envelope.recipient, "node-beta")
        self.assertTrue(init_envelope.verify_signature("shared-secret-ab"))
        sid = init_envelope.payload["session_id"]
        alpha_nonce = init_envelope.payload["nonce"]

        # Step 2: Beta handles and generates challenge response
        challenge_env = self.node_beta.handle_handshake_request(init_envelope)
        self.assertEqual(challenge_env.sender, "node-beta")
        self.assertEqual(challenge_env.recipient, "node-alpha")
        self.assertTrue(challenge_env.verify_signature("shared-secret-ab"))
        self.assertEqual(challenge_env.payload["session_id"], sid)
        
        # Verify proof of alpha nonce
        expected_alpha_proof = hmac.new(
            b"shared-secret-ab",
            alpha_nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(challenge_env.payload["initiator_nonce_proof"], expected_alpha_proof)

        # Step 3: Alpha completes and marks ESTABLISHED
        ack_env = self.node_alpha.complete_handshake(challenge_env)
        self.assertTrue(self.node_alpha.is_handshake_established("node-beta"))
        self.assertTrue(ack_env.verify_signature("shared-secret-ab"))

        # Step 4: Beta finalizes with ACK
        finalized = self.node_beta.finalize_responder_handshake(ack_env)
        self.assertTrue(finalized)
        self.assertTrue(self.node_beta.is_handshake_established("node-alpha"))

    def test_perform_mutual_handshake_helper(self):
        init_s, resp_s = self.node_alpha.perform_mutual_handshake(self.node_beta)
        self.assertEqual(init_s.state, HandshakeState.ESTABLISHED)
        self.assertEqual(resp_s.state, HandshakeState.ESTABLISHED)
        self.assertEqual(init_s.session_id, resp_s.session_id)
        self.assertTrue(self.node_alpha.is_handshake_established("node-beta"))
        self.assertTrue(self.node_beta.is_handshake_established("node-alpha"))

    def test_handshake_fails_with_invalid_signature(self):
        init_envelope = self.node_alpha.initiate_handshake("node-beta")
        # Tamper signature
        init_envelope.signature = "tampered_signature_0000"

        with self.assertRaises(ProtocolSecurityError):
            self.node_beta.handle_handshake_request(init_envelope)

    def test_handshake_fails_with_invalid_nonce_proof(self):
        init_envelope = self.node_alpha.initiate_handshake("node-beta")
        challenge_env = self.node_beta.handle_handshake_request(init_envelope)
        
        # Tamper nonce proof in payload and re-sign with secret
        challenge_env.payload["initiator_nonce_proof"] = "bad_proof"
        challenge_env.sign("shared-secret-ab")

        with self.assertRaises(ProtocolSecurityError):
            self.node_alpha.complete_handshake(challenge_env)

    def test_handshake_fails_unknown_peer_secret(self):
        node_gamma = FederatedOrchestrator(node_id="node-gamma")
        # Gamma has no secret for node-alpha
        init_envelope = self.node_alpha.initiate_handshake("node-beta")
        with self.assertRaises(ProtocolSecurityError):
            node_gamma.handle_handshake_request(init_envelope)


class TestRemoteTaskDispatchE2E(unittest.IsolatedAsyncioTestCase):
    """Test end-to-end remote task dispatch with HMAC-signed response verification."""

    async def asyncSetUp(self):
        self.orchestrator_alpha = FederatedOrchestrator(
            node_id="node-alpha",
            secret_key="shared-secret-ab",
        )
        self.orchestrator_beta = FederatedOrchestrator(
            node_id="node-beta",
            secret_key="shared-secret-ab",
        )

        # Pre-shared secrets
        self.orchestrator_alpha.register_peer_secret("node-beta", "shared-secret-ab")
        self.orchestrator_beta.register_peer_secret("node-alpha", "shared-secret-ab")

        # Mutual handshake establish
        self.orchestrator_alpha.perform_mutual_handshake(self.orchestrator_beta)

        # Register remote worker agent on node-beta
        self.orchestrator_alpha.register_peer_agent(
            agent_id="worker-beta-vision",
            name="Remote Vision Worker",
            node_id="node-beta",
            protocol=ProtocolType.ANP,
            capabilities=["multimodal_analysis", "vision"],
        )
        self.orchestrator_beta.register_peer_agent(
            agent_id="worker-beta-vision",
            name="Remote Vision Worker",
            node_id="node-beta",
            protocol=ProtocolType.ANP,
            capabilities=["multimodal_analysis", "vision"],
        )

    async def test_successful_remote_task_dispatch_generic(self):
        result = await self.orchestrator_alpha.dispatch_task_to_remote_peer(
            task_id="task-101",
            target_agent_id="worker-beta-vision",
            task_type="code_audit",
            parameters={"repo": "hermes/platform", "branch": "main"},
            peer_orchestrator=self.orchestrator_beta,
        )

        self.assertIsInstance(result, RemoteTaskResult)
        self.assertEqual(result.task_id, "task-101")
        self.assertTrue(result.success)
        self.assertTrue(result.verified_signature)
        self.assertEqual(result.remote_agent_id, "node-beta")

    async def test_successful_remote_task_dispatch_multimodal_perception(self):
        result = await self.orchestrator_alpha.dispatch_task_to_remote_peer(
            task_id="task-multimodal-42",
            target_agent_id="worker-beta-vision",
            task_type="multimodal_analysis",
            parameters={
                "modality": "vision",
                "target": "architecture_diagram.png",
                "input_text": "ANP 1.1 Federated Handshake Diagram",
            },
            peer_orchestrator=self.orchestrator_beta,
        )

        self.assertTrue(result.success)
        self.assertTrue(result.verified_signature)
        
        # Verify PerceptionArtifact structured extraction
        artifact = result.perception_artifact
        self.assertIsNotNone(artifact)
        self.assertIsInstance(artifact, PerceptionArtifact)
        self.assertEqual(artifact.modality, "vision")
        self.assertIn("architecture_diagram.png", artifact.summary)
        self.assertEqual(artifact.extracted_text, "ANP 1.1 Federated Handshake Diagram")
        self.assertEqual(artifact.produced_by, "node-beta")
        self.assertEqual(artifact.uncertainty, "low")

    async def test_task_dispatch_rejected_without_handshake(self):
        orchestrator_gamma = FederatedOrchestrator(
            node_id="node-gamma",
            secret_key="shared-secret-ag",
        )
        self.orchestrator_alpha.register_peer_secret("node-gamma", "shared-secret-ag")
        self.orchestrator_alpha.register_peer_agent(
            agent_id="worker-gamma-1",
            name="Gamma Worker",
            node_id="node-gamma",
            protocol=ProtocolType.ANP,
        )

        # Handshake not established yet
        with self.assertRaises(ProtocolSecurityError) as ctx:
            await self.orchestrator_alpha.dispatch_task_to_remote_peer(
                task_id="task-gamma-1",
                target_agent_id="worker-gamma-1",
                task_type="generic",
                parameters={},
                peer_orchestrator=orchestrator_gamma,
                require_mutual_handshake=True,
            )
        self.assertIn("mutual handshake with node 'node-gamma' not established", str(ctx.exception))

    async def test_task_response_signature_tampering_fails(self):
        # Tampered response generator
        def tampered_executor(req_envelope):
            resp = self.orchestrator_beta.execute_incoming_task(req_envelope)
            # Tamper the signature
            resp.signature = "tampered_signature"
            return resp

        with self.assertRaises(ProtocolSecurityError) as ctx:
            await self.orchestrator_alpha.dispatch_task_to_remote_peer(
                task_id="task-tampered",
                target_agent_id="worker-beta-vision",
                task_type="generic",
                parameters={},
                peer_orchestrator=self.orchestrator_beta,
                remote_executor=tampered_executor,
            )
        self.assertIn("Invalid HMAC signature on task response", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
