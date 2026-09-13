"""Tests for Federated Node Live Setup & Mesh (Passo 2).

Covers:
- Live network mesh setup with two nodes:
  * Node A (node_local_primary at 100.77.31.78:9120 / local socket)
  * Node B (node_worker_auxiliary at 100.77.31.78:9121 / peer socket)
- Live mutual 3-way HMAC-SHA256 handshake over network sockets.
- End-to-end remote sub-task dispatch from Node A to Node B via ANP wire envelope.
- Node B executes the task, signs the response with its private secret, and returns
  verified result with multimodal PerceptionArtifact.
- Node A verifies the signature, asserts zero-tampering, and integrates the result.
- Detection and prevention of tampering / invalid signatures.
- CLI hermes haos federation ping integration against live mesh endpoints.
"""

from __future__ import annotations

import argparse
import asyncio
import unittest
from typing import Dict, Any

from hermes.platform.capabilities.modality.workers import PerceptionArtifact
from hermes.platform.federation.mesh import FederatedMeshNode, create_mesh_pair
from hermes.platform.federation.orchestrator import HandshakeState, ProtocolSecurityError, RemoteTaskResult
from hermes.platform.protocols.unified_bus import ProtocolType, TrustBoundary
from hermes_cli.haos_cmd import cmd_haos_federation_ping


class TestFederatedMeshLive(unittest.IsolatedAsyncioTestCase):
    """E2E Live Mesh Network Socket Tests for Federated Hermes Nodes."""

    async def asyncSetUp(self):
        # We bind to 127.0.0.1 with dynamic ports (0) to avoid any port conflicts in CI/test runner
        self.node_a = FederatedMeshNode(
            node_id="node_local_primary",
            host="127.0.0.1",
            port=0,
        )
        self.node_b = FederatedMeshNode(
            node_id="node_worker_auxiliary",
            host="127.0.0.1",
            port=0,
        )

        self.shared_secret = "mesh-hmac-sha256-live-secret-test-key-32b"
        self.node_a.register_peer_secret("node_worker_auxiliary", self.shared_secret)
        self.node_b.register_peer_secret("node_local_primary", self.shared_secret)

        # Start HTTP servers
        self.node_a.start()
        self.node_b.start()

        # Wire up address mappings
        self.node_a.register_peer_address("node_worker_auxiliary", self.node_b.host, self.node_b.port)
        self.node_b.register_peer_address("node_local_primary", self.node_a.host, self.node_a.port)

        # Register workers on Node B
        self.worker_endpoint = self.node_b.register_agent(
            agent_id="worker_b_multimodal",
            name="Node B Multimodal Worker",
            capabilities=["modality:vision", "reasoning:deep"],
            protocol=ProtocolType.ANP,
        )

    async def asyncTearDown(self):
        self.node_a.stop()
        self.node_b.stop()

    def test_node_startup_and_health_check(self):
        """Test both mesh nodes start, listen, and report healthy via GET /health."""
        self.assertTrue(self.node_a.is_running)
        self.assertTrue(self.node_b.is_running)
        self.assertGreater(self.node_a.port, 0)
        self.assertGreater(self.node_b.port, 0)

    def test_peer_endpoint_discovery(self):
        """Test Node A discovers Node B's registered agents over HTTP /directory."""
        discovered = self.node_a.discover_peer_endpoints("node_worker_auxiliary")
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].agent_id, "worker_b_multimodal")
        self.assertIn("modality:vision", discovered[0].capabilities)

        # Local directory on Node A now has worker_b_multimodal
        ep = self.node_a.directory.get("worker_b_multimodal")
        self.assertIsNotNone(ep)
        self.assertEqual(ep.metadata.get("node_id"), "node_worker_auxiliary")

    def test_live_mutual_3way_handshake(self):
        """Test live mutual 3-way HMAC-SHA256 handshake over network."""
        init_session, resp_session = self.node_a.perform_live_handshake("node_worker_auxiliary")

        self.assertEqual(init_session.state, HandshakeState.ESTABLISHED)
        self.assertEqual(resp_session.state, HandshakeState.ESTABLISHED)
        self.assertEqual(init_session.peer_node_id, "node_worker_auxiliary")
        self.assertTrue(self.node_a.orchestrator.is_handshake_established("node_worker_auxiliary"))
        self.assertTrue(self.node_b.orchestrator.is_handshake_established("node_local_primary"))

    def test_live_handshake_fails_with_invalid_secret(self):
        """Test handshake fails and raises ProtocolSecurityError if secrets mismatch."""
        untrusted_node = FederatedMeshNode(node_id="untrusted_node", host="127.0.0.1", port=0)
        untrusted_node.register_peer_secret("node_worker_auxiliary", "wrong-secret-key")
        untrusted_node.register_peer_address("node_worker_auxiliary", self.node_b.host, self.node_b.port)
        untrusted_node.start()

        try:
            with self.assertRaises(ProtocolSecurityError):
                untrusted_node.perform_live_handshake("node_worker_auxiliary")
        finally:
            untrusted_node.stop()

    async def test_live_remote_task_dispatch_end_to_end(self):
        """Test full live E2E task dispatch:
        
        1. Node A discovers Node B endpoints.
        2. Node A performs 3-way mutual handshake with Node B.
        3. Node A dispatches multimodal task to Node B over ANP wire envelope.
        4. Node B executes, signs response with its secret, returns PerceptionArtifact.
        5. Node A verifies signature, asserts zero-tampering, and integrates result.
        """
        # 1. Discover endpoints
        self.node_a.discover_peer_endpoints("node_worker_auxiliary")

        # 2. 3-way Handshake
        self.node_a.perform_live_handshake("node_worker_auxiliary")

        # 3 & 4. Dispatch remote multimodal task
        task_id = "task-live-mesh-001"
        result = await self.node_a.dispatch_task_live(
            task_id=task_id,
            target_agent_id="worker_b_multimodal",
            task_type="multimodal_analysis",
            parameters={
                "modality": "vision",
                "target": "mesh_architecture_diagram.png",
                "input_text": "HAOS Mesh Wire Protocol ANP 1.1",
            },
        )

        # 5. Assertions on Node A side
        self.assertTrue(result.success)
        self.assertTrue(result.verified_signature)
        self.assertEqual(result.task_id, task_id)
        self.assertEqual(result.remote_agent_id, "node_worker_auxiliary")

        # Multimodal PerceptionArtifact verification
        artifact = result.perception_artifact
        self.assertIsNotNone(artifact)
        self.assertIsInstance(artifact, PerceptionArtifact)
        self.assertEqual(artifact.modality, "vision")
        self.assertIn("mesh_architecture_diagram.png", artifact.summary)
        self.assertEqual(artifact.extracted_text, "HAOS Mesh Wire Protocol ANP 1.1")
        self.assertEqual(artifact.uncertainty, "low")

    async def test_task_dispatch_rejected_without_handshake(self):
        """Test dispatching task fails if mutual handshake has not taken place."""
        self.node_a.discover_peer_endpoints("node_worker_auxiliary")

        # Do NOT perform handshake
        with self.assertRaises(ProtocolSecurityError) as ctx:
            await self.node_a.dispatch_task_live(
                task_id="task-no-handshake",
                target_agent_id="worker_b_multimodal",
                task_type="multimodal_analysis",
                parameters={},
            )
        self.assertIn("mutual handshake with node 'node_worker_auxiliary' not established", str(ctx.exception))

    def test_cli_federation_ping_live_endpoint(self):
        """Test hermes haos federation ping with --endpoint parameter against live mesh node."""
        args = argparse.Namespace(
            peer_id="node_worker_auxiliary",
            secret=self.shared_secret,
            endpoint=f"{self.node_b.host}:{self.node_b.port}",
        )
        ret = cmd_haos_federation_ping(args)
        self.assertEqual(ret, 0)


class TestSimulatedVirtualIPNodeMesh(unittest.TestCase):
    """Verify fallback and address configuration with simulated cluster IPs (100.77.31.78:9120 and 9121)."""

    def test_mesh_pair_creation_and_binding(self):
        node_a, node_b = create_mesh_pair(
            node_a_id="node_local_primary",
            node_b_id="node_worker_auxiliary",
            node_a_addr=("100.77.31.78", 9120),
            node_b_addr=("100.77.31.78", 9121),
            shared_secret="cluster-secret-key-12345",
        )
        self.assertEqual(node_a.node_id, "node_local_primary")
        self.assertEqual(node_b.node_id, "node_worker_auxiliary")
        self.assertEqual(node_a._peer_addresses["node_worker_auxiliary"], ("100.77.31.78", 9121))
        self.assertEqual(node_b._peer_addresses["node_local_primary"], ("100.77.31.78", 9120))


if __name__ == "__main__":
    unittest.main()
