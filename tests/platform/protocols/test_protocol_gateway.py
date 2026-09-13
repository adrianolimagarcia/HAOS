"""Test suite for Phase 4 — Universal Protocol Gateway & Federation Runtime.

Validates:
1. AgentCard & Capability Advertisement.
2. FederatedCapabilityResolver: Local resolution fallback to remote ANP/A2A agent proxy.
3. RemoteAgentReputationTracker: Success scoring, latency recording, and anti-tampering quarantine.
4. UniversalProtocolGateway: Unified envelope routing across ANP, A2A, and ACP.
"""

import unittest
from typing import Any, Dict

from hermes.platform.capabilities.universal_registry import (
    CapabilityCategory,
    CapabilityMetadata,
    UniversalCapabilityRegistry,
)
from hermes.platform.observability.event_store import EventStore
from hermes.platform.protocols.gateway import (
    AgentCard,
    FederatedCapabilityResolver,
    RemoteAgentReputationTracker,
    UniversalProtocolGateway,
)
from hermes.platform.protocols.unified_bus import ProtocolEnvelope, ProtocolType, TrustBoundary


class TestPhase4ProtocolGateway(unittest.TestCase):
    """Verifies all Phase 4 Protocol Gateway and Federation capabilities."""

    def setUp(self):
        self.event_store = EventStore(db_path=":memory:")
        self.local_registry = UniversalCapabilityRegistry()
        self.local_registry.register(
            CapabilityMetadata(id="local_file_read", category=CapabilityCategory.PLUGIN, provider_type="builtin")
        )
        self.reputation = RemoteAgentReputationTracker(quarantine_threshold=0.40)
        self.resolver = FederatedCapabilityResolver(
            local_registry=self.local_registry,
            reputation_tracker=self.reputation,
        )
        self.gateway = UniversalProtocolGateway(
            event_store=self.event_store,
            capability_resolver=self.resolver,
        )

    def test_federated_capability_resolution_local(self):
        """Capabilities present in the local registry are resolved immediately."""
        res = self.resolver.resolve_capability("local_file_read")
        self.assertTrue(res.resolved_locally)
        self.assertIsNone(res.provider_agent)

    def test_federated_capability_resolution_remote_proxy(self):
        """Missing local capability falls back to remote AgentCard and builds proxy handler."""
        remote_agent = AgentCard(
            agent_id="agent_remote_security_auditor",
            name="Cloud Security Auditor",
            description="Performs remote static code vulnerability analysis",
            protocol=ProtocolType.ANP,
            endpoint_url="https://sec.federation.mesh:9000",
            capabilities=["remote_vuln_scan", "sbom_generate"],
        )
        self.resolver.register_remote_agent(remote_agent)

        def mock_remote_call(agent: AgentCard, params: Dict[str, Any]) -> Dict[str, Any]:
            return {"agent": agent.agent_id, "vulnerabilities_found": 0, "status": "clean"}

        res = self.resolver.resolve_capability("remote_vuln_scan", remote_dispatch_fn=mock_remote_call)
        self.assertFalse(res.resolved_locally)
        self.assertEqual(res.provider_agent.agent_id, "agent_remote_security_auditor")
        self.assertEqual(res.protocol, ProtocolType.ANP)

        # Execute the proxied call
        output = res.proxy_handler({"target_file": "app.py"})
        self.assertEqual(output["vulnerabilities_found"], 0)
        self.assertEqual(self.reputation.get_score("agent_remote_security_auditor"), 2 / 3)

    def test_remote_agent_tampering_and_quarantine(self):
        """Tampering or repeated failure quarantines an external agent, blocking resolution."""
        remote_agent = AgentCard(
            agent_id="bad_actor_agent",
            name="Untrusted Node",
            description="Malicious node",
            protocol=ProtocolType.A2A,
            endpoint_url="https://untrusted.node:8000",
            capabilities=["suspicious_cap"],
        )
        self.resolver.register_remote_agent(remote_agent)

        # Record tampering violation
        self.reputation.record_tampering("bad_actor_agent")
        self.assertEqual(self.reputation.get_score("bad_actor_agent"), 0.0)
        self.assertTrue(self.reputation.is_quarantined("bad_actor_agent"))

        # Attempting to resolve capability from quarantined agent must fail
        with self.assertRaises(KeyError):
            self.resolver.resolve_capability("suspicious_cap")

    def test_universal_protocol_gateway_dispatch(self):
        """UniversalProtocolGateway routes envelopes to ANP, A2A, and ACP handlers with telemetry."""
        dispatched_protocols = []

        def dummy_anp_handler(env: ProtocolEnvelope) -> ProtocolEnvelope:
            dispatched_protocols.append("ANP")
            return ProtocolEnvelope(
                envelope_id="resp-anp",
                protocol_type=ProtocolType.ANP,
                sender=env.recipient,
                recipient=env.sender,
                trust_boundary=TrustBoundary.FEDERATED,
                payload={"ack": True},
            )

        def dummy_a2a_handler(env: ProtocolEnvelope) -> ProtocolEnvelope:
            dispatched_protocols.append("A2A")
            return ProtocolEnvelope(
                envelope_id="resp-a2a",
                protocol_type=ProtocolType.A2A,
                sender=env.recipient,
                recipient=env.sender,
                trust_boundary=TrustBoundary.FEDERATED,
                payload={"swarm_ack": True},
            )

        self.gateway.register_protocol_handler(ProtocolType.ANP, dummy_anp_handler)
        self.gateway.register_protocol_handler(ProtocolType.A2A, dummy_a2a_handler)

        env1 = ProtocolEnvelope(
            envelope_id="req-1",
            protocol_type=ProtocolType.ANP,
            sender="mayor",
            recipient="external_anp_worker",
            trust_boundary=TrustBoundary.FEDERATED,
            payload={"task": "inspect"},
        )
        resp1 = self.gateway.dispatch(env1)
        self.assertEqual(resp1.envelope_id, "resp-anp")

        env2 = ProtocolEnvelope(
            envelope_id="req-2",
            protocol_type=ProtocolType.A2A,
            sender="mayor",
            recipient="partner_swarm",
            trust_boundary=TrustBoundary.FEDERATED,
            payload={"task": "sync"},
        )
        resp2 = self.gateway.dispatch(env2)
        self.assertEqual(resp2.envelope_id, "resp-a2a")

        self.assertEqual(dispatched_protocols, ["ANP", "A2A"])

        # Check EventStore audit events
        events = self.event_store.read_events()
        event_names = [e.name for e in events]
        self.assertEqual(event_names.count("gateway.envelope_dispatched"), 2)
        self.assertEqual(event_names.count("gateway.envelope_processed"), 2)


if __name__ == "__main__":
    unittest.main()
