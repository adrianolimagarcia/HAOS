"""Tests for Marco 6 (Model/Provider Fabric) and Marco 7 (Protocol Fabric).

Validates:
- Architectural axiom: Model != Provider.
- ExactModelFailoverRouter routes request strictly to exact-model alternative providers
  without silent model degradation when circuit breaker or rate limit trips.
- ProtocolEnvelope serialization and signature verification.
- ProtocolRouter dispatch across verified boundaries and protocols (INTERNAL, MCP, ACP, A2A, ANP).

Strict stdlib-only; PEP-420 namespace compliance.
"""

import asyncio
import time
import unittest

from hermes.platform.models.circuit_breaker import CircuitBreaker
from hermes.platform.models.profiles import ModelIdentity
from hermes.platform.models.unified_fabric import (
    ExactModelFailoverRouter,
    ModelDegradationPreventedError,
    ModelProfile,
    ModelRouteExhaustedError,
    ProviderPriorityEntry,
    RoutingDecision,
)
from hermes.platform.protocols.unified_bus import (
    ProtocolEnvelope,
    ProtocolRouter,
    ProtocolRoutingError,
    ProtocolSecurityError,
    ProtocolType,
    TrustBoundary,
)


class TestModelFabric(unittest.TestCase):
    """Marco 6: Model / Provider Fabric tests."""

    def setUp(self):
        self.circuit_breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)
        self.router = ExactModelFailoverRouter(circuit_breaker=self.circuit_breaker)

        # Claude 3.7 Sonnet: Anthropic (priority 1) -> OpenRouter (priority 2) -> Bedrock (priority 3)
        self.claude_profile = ModelProfile(
            id="claude-3-7-sonnet",
            model_name="anthropic/claude-3-7-sonnet",
            context_window=200000,
            max_output=8192,
            parameters={"temperature": 0.2},
            posture_assignments=["architect", "reviewer"],
            provider_priority_list=[
                ProviderPriorityEntry("anthropic", "claude-3-7-sonnet", priority=1),
                ProviderPriorityEntry("openrouter", "anthropic/claude-3.7-sonnet", priority=2),
                ProviderPriorityEntry("bedrock", "anthropic.claude-3-7-sonnet-v1:0", priority=3),
            ],
            substitute_allowed=False,
        )

    def test_model_not_equal_provider_axiom(self):
        """Model identity remains constant while providers are distinct execution venues."""
        profile = self.claude_profile
        self.assertEqual(profile.model_name, "anthropic/claude-3-7-sonnet")
        self.assertEqual(len(profile.provider_priority_list), 3)

        # Verify ordering
        ordered = profile.ordered_providers()
        self.assertEqual(ordered[0].provider_id, "anthropic")
        self.assertEqual(ordered[1].provider_id, "openrouter")
        self.assertEqual(ordered[2].provider_id, "bedrock")

        # First route selects primary provider
        decision = self.router.select_route(profile)
        self.assertEqual(decision.selected_provider_id, "anthropic")
        self.assertEqual(decision.selected_provider_model_id, "claude-3-7-sonnet")
        self.assertEqual(decision.attempts_considered, 1)
        self.assertEqual(decision.bypassed_providers, [])

    def test_exact_model_failover_on_rate_limit(self):
        """When primary provider is rate-limited, failover to exact model on secondary provider."""
        profile = self.claude_profile

        # Primary is selected initially
        decision1 = self.router.select_route(profile)
        self.assertEqual(decision1.selected_provider_id, "anthropic")

        # Mark Anthropic as rate limited
        self.router.record_failure("anthropic", profile, is_rate_limit=True, rate_limit_cooldown=30.0)

        # Next route should failover to OpenRouter (priority 2) with exact model
        decision2 = self.router.select_route(profile)
        self.assertEqual(decision2.selected_provider_id, "openrouter")
        self.assertEqual(decision2.selected_provider_model_id, "anthropic/claude-3.7-sonnet")
        self.assertIn("anthropic", decision2.bypassed_providers)

    def test_exact_model_failover_on_circuit_breaker(self):
        """When primary trips circuit breaker, failover to exact model on tertiary provider."""
        profile = self.claude_profile

        # Trip Anthropic circuit breaker (threshold = 2)
        self.router.record_failure("anthropic", profile)
        self.router.record_failure("anthropic", profile)

        # Route should now be OpenRouter
        decision = self.router.select_route(profile)
        self.assertEqual(decision.selected_provider_id, "openrouter")

        # Trip OpenRouter circuit breaker
        self.router.record_failure("openrouter", profile)
        self.router.record_failure("openrouter", profile)

        # Route should now failover to Bedrock
        decision_bedrock = self.router.select_route(profile)
        self.assertEqual(decision_bedrock.selected_provider_id, "bedrock")
        self.assertEqual(decision_bedrock.selected_provider_model_id, "anthropic.claude-3-7-sonnet-v1:0")
        self.assertEqual(decision_bedrock.bypassed_providers, ["anthropic", "openrouter"])

    def test_model_degradation_strictly_prevented(self):
        """When all exact-model providers are exhausted, refuse silent degradation to another model."""
        profile = self.claude_profile

        # Trip all 3 providers
        for provider in ["anthropic", "openrouter", "bedrock"]:
            self.router.record_failure(provider, profile)
            self.router.record_failure(provider, profile)

        # Router must raise ModelDegradationPreventedError, NOT silently switch model
        with self.assertRaises(ModelDegradationPreventedError) as ctx:
            self.router.select_route(profile)
        self.assertIn("Silent model degradation strictly prevented", str(ctx.exception))

    def test_provider_recovery_resets_priority(self):
        """When a primary provider recovers (record_success), router resumes using primary."""
        profile = self.claude_profile

        # Trip Anthropic
        self.router.record_failure("anthropic", profile)
        self.router.record_failure("anthropic", profile)
        self.assertEqual(self.router.select_route(profile).selected_provider_id, "openrouter")

        # Anthropic recovers
        self.router.record_success("anthropic", profile)
        self.assertEqual(self.router.select_route(profile).selected_provider_id, "anthropic")


class TestProtocolFabric(unittest.TestCase):
    """Marco 7: Unified Protocol Fabric tests."""

    def setUp(self):
        self.router = ProtocolRouter()
        self.secret_key = "sovereign-agent-secret-key-12345"
        self.router.register_secret("agent-lead", self.secret_key)

    def test_envelope_serialization_and_signing(self):
        """ProtocolEnvelope serializes, signs and verifies correctly."""
        envelope = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender="agent-lead",
            recipient="agent-worker-1",
            payload={"action": "delegate_task", "task_id": "T-100"},
            trust_boundary=TrustBoundary.AGENT_SANDBOX,
        )
        envelope.sign(self.secret_key)

        self.assertIsNotNone(envelope.signature)
        self.assertTrue(envelope.verify_signature(self.secret_key))
        self.assertFalse(envelope.verify_signature("wrong-key"))

        # Test to_dict / from_dict roundtrip
        data = envelope.to_dict()
        reconstructed = ProtocolEnvelope.from_dict(data)

        self.assertEqual(reconstructed.protocol_type, ProtocolType.ANP)
        self.assertEqual(reconstructed.sender, "agent-lead")
        self.assertEqual(reconstructed.recipient, "agent-worker-1")
        self.assertEqual(reconstructed.payload, {"action": "delegate_task", "task_id": "T-100"})
        self.assertEqual(reconstructed.signature, envelope.signature)
        self.assertTrue(reconstructed.verify_signature(self.secret_key))

    def test_protocol_router_dispatch_across_protocols(self):
        """ProtocolRouter dispatches across INTERNAL, MCP, ACP, A2A, ANP."""
        received = []

        def mcp_handler(env: ProtocolEnvelope):
            received.append(("mcp", env.payload))
            return "mcp_ok"

        def anp_handler(env: ProtocolEnvelope):
            received.append(("anp", env.payload))
            return "anp_ok"

        self.router.register_route(
            recipient_pattern="system/mcp/*",
            handler=mcp_handler,
            allowed_protocols=[ProtocolType.MCP],
            minimum_trust=TrustBoundary.LOCAL_SECURE,
        )

        self.router.register_route(
            recipient_pattern="network/anp/*",
            handler=anp_handler,
            allowed_protocols=[ProtocolType.ANP],
            minimum_trust=TrustBoundary.FEDERATED,
        )

        # Dispatch valid MCP
        mcp_env = ProtocolEnvelope(
            protocol_type=ProtocolType.MCP,
            sender="orchestrator",
            recipient="system/mcp/tools",
            payload={"tool": "read_file"},
            trust_boundary=TrustBoundary.LOCAL_SECURE,
        )
        res_mcp = self.router.dispatch_sync(mcp_env)
        self.assertEqual(res_mcp, ["mcp_ok"])

        # Dispatch valid ANP (signed with shared secret for FEDERATED trust boundary)
        self.router.register_shared_secret("peer-agent", "secret_peer_anp")
        anp_env = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender="peer-agent",
            recipient="network/anp/discover",
            payload={"query": "capabilities"},
            trust_boundary=TrustBoundary.FEDERATED,
        )
        anp_env.sign("secret_peer_anp")
        res_anp = self.router.dispatch_sync(anp_env)
        self.assertEqual(res_anp, ["anp_ok"])

        self.assertEqual(len(received), 2)

    def test_trust_boundary_enforcement(self):
        """Untrusted or lower-trust envelopes cannot breach higher-trust boundaries."""
        def kernel_handler(env: ProtocolEnvelope):
            return "kernel_executed"

        self.router.register_route(
            recipient_pattern="kernel/exec",
            handler=kernel_handler,
            allowed_protocols=[ProtocolType.INTERNAL],
            minimum_trust=TrustBoundary.KERNEL,
        )

        # Envelope coming from AGENT_SANDBOX should be rejected
        untrusted_env = ProtocolEnvelope(
            protocol_type=ProtocolType.INTERNAL,
            sender="agent-sandbox-1",
            recipient="kernel/exec",
            payload={"cmd": "reboot"},
            trust_boundary=TrustBoundary.AGENT_SANDBOX,
        )

        with self.assertRaises(ProtocolSecurityError) as ctx:
            self.router.dispatch_sync(untrusted_env)
        self.assertIn("Insufficient trust boundary", str(ctx.exception))

        # Envelope from KERNEL should succeed
        kernel_env = ProtocolEnvelope(
            protocol_type=ProtocolType.INTERNAL,
            sender="kernel-core",
            recipient="kernel/exec",
            payload={"cmd": "status"},
            trust_boundary=TrustBoundary.KERNEL,
        )
        res = self.router.dispatch_sync(kernel_env)
        self.assertEqual(res, ["kernel_executed"])

    def test_signature_requirement_enforcement(self):
        """Routes requiring signatures reject unsigned or invalid-signature envelopes."""
        def secure_handler(env: ProtocolEnvelope):
            return "authorized"

        self.router.register_route(
            recipient_pattern="agents/secure-task",
            handler=secure_handler,
            allowed_protocols=[ProtocolType.A2A],
            minimum_trust=TrustBoundary.AGENT_SANDBOX,
            require_signature=True,
        )

        # Unsigned envelope
        unsigned_env = ProtocolEnvelope(
            protocol_type=ProtocolType.A2A,
            sender="agent-lead",
            recipient="agents/secure-task",
            payload={"step": 1},
            trust_boundary=TrustBoundary.AGENT_SANDBOX,
        )
        with self.assertRaises(ProtocolSecurityError) as ctx:
            self.router.dispatch_sync(unsigned_env)
        self.assertIn("strictly requires signature", str(ctx.exception))

        # Properly signed envelope
        unsigned_env.sign(self.secret_key)
        res = self.router.dispatch_sync(unsigned_env)
        self.assertEqual(res, ["authorized"])

        # Tampered envelope / invalid signature
        unsigned_env.payload = {"step": 999, "tampered": True}
        with self.assertRaises(ProtocolSecurityError) as ctx:
            self.router.dispatch_sync(unsigned_env)
        self.assertIn("Invalid signature", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
