"""Integration tests for Unified Protocol Wire Bus Integration (Passo 2).

Tests:
1. Cross-protocol translation: ACP -> INTERNAL -> A2A -> ANP and back.
2. Trust boundary validation and HMAC-SHA256 signature verification for FEDERATED and UNTRUSTED messages.
3. FederatedAgentDirectory discovery, capability lookup, and routing across agents.
4. EventBus integration with unified ProtocolRouter.
"""

import asyncio
import unittest

from hermes.platform.protocols.bus import EventBus
from hermes.platform.protocols.unified_bus import (
    CrossProtocolBridge,
    FederatedAgentDirectory,
    FederatedAgentEndpoint,
    ProtocolEnvelope,
    ProtocolRouter,
    ProtocolRoutingError,
    ProtocolSecurityError,
    ProtocolType,
    TrustBoundary,
)


class TestCrossProtocolBridge(unittest.IsolatedAsyncioTestCase):
    """Test cross-protocol translation (ACP -> INTERNAL -> A2A -> ANP)."""

    async def test_acp_to_internal_translation(self):
        bridge = CrossProtocolBridge()
        acp_event = {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"session_id": "sess-123", "delta": "hello world"},
            "id": 42,
        }
        envelope = bridge.acp_to_internal(
            acp_event,
            sender="acp_client_editor",
            recipient="kernel/agent_manager",
            trust_boundary=TrustBoundary.LOCAL_SECURE,
        )

        self.assertEqual(envelope.protocol_type, ProtocolType.INTERNAL)
        self.assertEqual(envelope.sender, "acp_client_editor")
        self.assertEqual(envelope.recipient, "kernel/agent_manager")
        self.assertEqual(envelope.trust_boundary, TrustBoundary.LOCAL_SECURE)
        self.assertEqual(envelope.payload["session_id"], "sess-123")
        self.assertEqual(envelope.metadata["source_protocol"], "ACP")
        self.assertEqual(envelope.metadata["method"], "session/update")
        self.assertEqual(envelope.metadata["raw_id"], 42)

    async def test_internal_to_a2a_translation(self):
        bridge = CrossProtocolBridge()
        internal_env = ProtocolEnvelope(
            protocol_type=ProtocolType.INTERNAL,
            sender="agent_alpha",
            recipient="agent_beta",
            payload={"content": "Please analyze this code snippet.", "task_id": "t-1"},
            trust_boundary=TrustBoundary.LOCAL_SECURE,
        )
        a2a_env = bridge.internal_to_a2a(internal_env, role="user")

        self.assertEqual(a2a_env.protocol_type, ProtocolType.A2A)
        self.assertEqual(a2a_env.sender, "agent_alpha")
        self.assertEqual(a2a_env.recipient, "agent_beta")
        self.assertEqual(a2a_env.payload["role"], "user")
        self.assertEqual(a2a_env.payload["kind"], "message")
        self.assertEqual(len(a2a_env.payload["parts"]), 1)
        self.assertEqual(a2a_env.payload["parts"][0]["kind"], "text")
        self.assertIn("Please analyze this code snippet.", a2a_env.payload["parts"][0]["text"])
        self.assertEqual(a2a_env.metadata["source_protocol"], "INTERNAL")

    async def test_a2a_to_anp_translation(self):
        bridge = CrossProtocolBridge()
        a2a_env = ProtocolEnvelope(
            protocol_type=ProtocolType.A2A,
            sender="agent_alpha",
            recipient="agent_gamma",
            payload={"role": "user", "parts": [{"kind": "text", "text": "Hello federated ANP peer"}]},
            trust_boundary=TrustBoundary.LOCAL_SECURE,
        )
        anp_env = bridge.a2a_to_anp(a2a_env, domain="nous.net")

        self.assertEqual(anp_env.protocol_type, ProtocolType.ANP)
        self.assertTrue(anp_env.recipient.startswith("did:wba:nous.net:"))
        self.assertIn("agent_gamma", anp_env.recipient)
        self.assertEqual(anp_env.payload["meta"]["profile"], "anp.messaging.v1")
        self.assertEqual(anp_env.payload["body"], a2a_env.payload)
        self.assertEqual(anp_env.metadata["source_protocol"], "A2A")

    async def test_anp_to_internal_translation(self):
        bridge = CrossProtocolBridge()
        anp_env = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender="did:wba:remote.org:peer_1",
            recipient="did:wba:local.hermes:hub",
            payload={
                "meta": {"profile": "anp.messaging.v1"},
                "body": {"event": "status_update", "status": "active"},
            },
            trust_boundary=TrustBoundary.FEDERATED,
        )
        internal_env = bridge.anp_to_internal(anp_env, recipient="kernel/event_bus")

        self.assertEqual(internal_env.protocol_type, ProtocolType.INTERNAL)
        self.assertEqual(internal_env.sender, "did:wba:remote.org:peer_1")
        self.assertEqual(internal_env.recipient, "kernel/event_bus")
        self.assertEqual(internal_env.payload, {"event": "status_update", "status": "active"})
        self.assertEqual(internal_env.metadata["source_protocol"], "ANP")

    async def test_full_pipeline_cross_protocol_seamless(self):
        """Test seamless pipeline: ACP -> INTERNAL -> A2A -> ANP."""
        bridge = CrossProtocolBridge()
        secret = "super_secret_bridge_key"

        # 1. Source is an ACP event
        acp_input = ProtocolEnvelope(
            protocol_type=ProtocolType.ACP,
            sender="vscode_ide",
            recipient="agent_executor",
            payload={"action": "run_test", "suite": "integration"},
            trust_boundary=TrustBoundary.LOCAL_SECURE,
        )

        # 2. Bridge pipeline directly to ANP
        anp_out = await bridge.bridge_pipeline(
            source_envelope=acp_input,
            target_protocol=ProtocolType.ANP,
            target_recipient="did:wba:federated.ai:executor",
            secret_key=secret,
        )

        self.assertEqual(anp_out.protocol_type, ProtocolType.ANP)
        self.assertEqual(anp_out.recipient, "did:wba:federated.ai:executor")
        self.assertIsNotNone(anp_out.signature)
        self.assertTrue(anp_out.verify_signature(secret))

        # Check payload structure was translated through A2A to ANP
        self.assertIn("meta", anp_out.payload)
        self.assertIn("body", anp_out.payload)
        self.assertEqual(anp_out.payload["body"]["role"], "user")


class TestTrustBoundariesAndCryptographicValidation(unittest.IsolatedAsyncioTestCase):
    """Test verified trust boundaries and HMAC-SHA256 signature verification."""

    async def asyncSetUp(self):
        self.router = ProtocolRouter()
        self.received_messages = []

        async def handler(env: ProtocolEnvelope):
            self.received_messages.append(env)
            return {"status": "ok", "id": env.envelope_id}

        self.handler = handler

    async def test_trust_hierarchy_enforcement(self):
        # Route requires KERNEL level trust
        self.router.register_route(
            recipient_pattern="kernel/secure_vault",
            handler=self.handler,
            allowed_protocols=[ProtocolType.INTERNAL],
            min_trust_boundary=TrustBoundary.KERNEL,
        )

        # 1. Valid dispatch with KERNEL trust
        env_kernel = ProtocolEnvelope(
            protocol_type=ProtocolType.INTERNAL,
            sender="root_kernel",
            recipient="kernel/secure_vault",
            payload={"op": "unlock"},
            trust_boundary=TrustBoundary.KERNEL,
        )
        res = await self.router.dispatch(env_kernel)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "ok")

        # 2. Insufficient trust (AGENT_SANDBOX < KERNEL) must raise ProtocolSecurityError
        env_sandbox = ProtocolEnvelope(
            protocol_type=ProtocolType.INTERNAL,
            sender="sandbox_worker",
            recipient="kernel/secure_vault",
            payload={"op": "escalate"},
            trust_boundary=TrustBoundary.AGENT_SANDBOX,
        )
        with self.assertRaises(ProtocolSecurityError) as ctx:
            await self.router.dispatch(env_sandbox)
        self.assertIn("Insufficient trust", str(ctx.exception))

    async def test_federated_message_enforces_cryptographic_signature(self):
        self.router.register_route(
            recipient_pattern="agent/federated_receiver",
            handler=self.handler,
            allowed_protocols=[ProtocolType.A2A, ProtocolType.ANP],
            min_trust_boundary=TrustBoundary.FEDERATED,
        )

        sender_id = "did:wba:peer.org:agent_9"
        secret = "crypto_shared_secret_key_123"
        self.router.register_secret(sender_id, secret)

        # 1. Dispatch FEDERATED message without signature -> fails
        unsigned_env = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender=sender_id,
            recipient="agent/federated_receiver",
            payload={"query": "data"},
            trust_boundary=TrustBoundary.FEDERATED,
        )
        with self.assertRaises(ProtocolSecurityError) as ctx:
            await self.router.dispatch(unsigned_env)
        self.assertIn("Signature strictly required for federated message", str(ctx.exception))

        # 2. Dispatch with invalid signature -> fails
        tampered_env = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender=sender_id,
            recipient="agent/federated_receiver",
            payload={"query": "data"},
            trust_boundary=TrustBoundary.FEDERATED,
            signature="tampered_bad_hex_signature",
        )
        with self.assertRaises(ProtocolSecurityError) as ctx:
            await self.router.dispatch(tampered_env)
        self.assertIn("Invalid signature", str(ctx.exception))

        # 3. Dispatch with valid HMAC-SHA256 signature -> succeeds
        valid_env = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender=sender_id,
            recipient="agent/federated_receiver",
            payload={"query": "data"},
            trust_boundary=TrustBoundary.FEDERATED,
        )
        valid_env.sign(secret)
        self.assertTrue(valid_env.verify_signature(secret))

        res = await self.router.dispatch(valid_env)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "ok")

    async def test_untrusted_message_requires_known_secret_and_signature(self):
        self.router.register_route(
            recipient_pattern="gateway/public",
            handler=self.handler,
            allowed_protocols=[ProtocolType.ACP, ProtocolType.INTERNAL],
            min_trust_boundary=TrustBoundary.UNTRUSTED,
        )

        sender_id = "unknown_caller"
        untrusted_env = ProtocolEnvelope(
            protocol_type=ProtocolType.ACP,
            sender=sender_id,
            recipient="gateway/public",
            payload={"hello": "world"},
            trust_boundary=TrustBoundary.UNTRUSTED,
        )
        untrusted_env.signature = "dummy_signature"

        with self.assertRaises(ProtocolSecurityError) as ctx:
            await self.router.dispatch(untrusted_env)
        self.assertIn("No secret registered", str(ctx.exception))


class TestFederatedAgentDirectoryAndRouting(unittest.IsolatedAsyncioTestCase):
    """Test FederatedAgentDirectory discovery, capability lookup, and cross-agent routing."""

    async def test_directory_discovery_and_capability_query(self):
        router = ProtocolRouter()
        directory = FederatedAgentDirectory(router=router)

        ep1 = FederatedAgentEndpoint(
            agent_id="agent:weather_service",
            name="Weather Service Agent",
            protocol=ProtocolType.A2A,
            trust_boundary=TrustBoundary.FEDERATED,
            endpoint_url="http://127.0.0.1:9090",
            capabilities=["weather", "forecast", "radar"],
            secret_key="secret_weather_1",
        )
        ep2 = FederatedAgentEndpoint(
            agent_id="agent:code_reviewer",
            name="Code Reviewer Agent",
            protocol=ProtocolType.ANP,
            trust_boundary=TrustBoundary.FEDERATED,
            endpoint_url="did:wba:nous.net:reviewer",
            capabilities=["code_review", "linter", "ast_analysis"],
            secret_key="secret_reviewer_2",
        )
        directory.register(ep1)
        directory.register(ep2)

        # Verify registration and secret synchronization with router
        self.assertEqual(directory.get("agent:weather_service"), ep1)
        self.assertIn("agent:weather_service", router._shared_secrets)
        self.assertEqual(router._shared_secrets["agent:weather_service"], "secret_weather_1")

        # Query by capability
        weather_agents = directory.find_by_capability("weather")
        self.assertEqual(len(weather_agents), 1)
        self.assertEqual(weather_agents[0].agent_id, "agent:weather_service")

        review_agents = directory.find_by_capability("code_review")
        self.assertEqual(len(review_agents), 1)
        self.assertEqual(review_agents[0].agent_id, "agent:code_reviewer")

        # Query by protocol
        anp_agents = directory.find_by_protocol(ProtocolType.ANP)
        self.assertEqual(len(anp_agents), 1)
        self.assertEqual(anp_agents[0].agent_id, "agent:code_reviewer")

        # Unregister
        directory.unregister("agent:weather_service")
        self.assertIsNone(directory.get("agent:weather_service"))

    async def test_directory_assisted_routing_and_bridge_flow(self):
        router = ProtocolRouter()
        directory = FederatedAgentDirectory(router=router)
        bridge = CrossProtocolBridge(router=router, directory=directory)

        received = []

        async def anp_destination_handler(env: ProtocolEnvelope):
            received.append(env)
            return {"received_id": env.envelope_id}

        router.register_route(
            recipient_pattern="did:wba:nous.net:analyst",
            handler=anp_destination_handler,
            allowed_protocols=[ProtocolType.ANP],
            min_trust_boundary=TrustBoundary.FEDERATED,
        )

        analyst_endpoint = FederatedAgentEndpoint(
            agent_id="agent:analyst_source",
            name="Data Analyst",
            protocol=ProtocolType.ANP,
            trust_boundary=TrustBoundary.FEDERATED,
            secret_key="analyst_key_xyz",
        )
        directory.register(analyst_endpoint)

        # Step 1: Start with ACP event from user editor
        acp_event = {
            "jsonrpc": "2.0",
            "method": "analyze_repo",
            "params": {"repo": "hermes-turbo"},
            "id": 1,
        }
        internal_env = bridge.acp_to_internal(
            acp_event,
            sender=analyst_endpoint.agent_id,
            recipient="internal/bus",
            trust_boundary=TrustBoundary.FEDERATED,
        )

        # Step 2: Bridge to ANP target
        anp_envelope = await bridge.bridge_pipeline(
            source_envelope=internal_env,
            target_protocol=ProtocolType.ANP,
            target_recipient="did:wba:nous.net:analyst",
            secret_key=analyst_endpoint.secret_key,
        )

        # Step 3: Dispatch to router
        res = await router.dispatch(anp_envelope)
        self.assertEqual(len(res), 1)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].protocol_type, ProtocolType.ANP)
        self.assertEqual(received[0].recipient, "did:wba:nous.net:analyst")


class TestEventBusUnifiedRouterIntegration(unittest.IsolatedAsyncioTestCase):
    """Test EventBus integration with unified ProtocolRouter."""

    async def test_event_bus_forwards_protocol_envelope_to_unified_router(self):
        router = ProtocolRouter()
        event_bus = EventBus(router=router)

        dispatched_envelopes = []

        async def destination_handler(env: ProtocolEnvelope):
            dispatched_envelopes.append(env)
            return "handled"

        router.register_route(
            recipient_pattern="subsystem/notifications",
            handler=destination_handler,
            allowed_protocols=[ProtocolType.INTERNAL],
            min_trust_boundary=TrustBoundary.KERNEL,
        )

        # 1. Publish ProtocolEnvelope via EventBus
        env = ProtocolEnvelope(
            protocol_type=ProtocolType.INTERNAL,
            sender="kernel/monitor",
            recipient="subsystem/notifications",
            payload={"alert": "disk_space_low"},
            trust_boundary=TrustBoundary.KERNEL,
        )

        await event_bus.publish(env)
        self.assertEqual(len(dispatched_envelopes), 1)
        self.assertEqual(dispatched_envelopes[0].payload["alert"], "disk_space_low")

    async def test_event_bus_forwards_dict_with_recipient(self):
        router = ProtocolRouter()
        event_bus = EventBus()
        event_bus.attach_router(router)

        dispatched_envelopes = []

        async def destination_handler(env: ProtocolEnvelope):
            dispatched_envelopes.append(env)
            return "handled"

        router.register_route(
            recipient_pattern="agent/worker",
            handler=destination_handler,
            allowed_protocols=[ProtocolType.INTERNAL],
            min_trust_boundary=TrustBoundary.KERNEL,
        )

        event_dict = {
            "recipient": "agent/worker",
            "protocol_type": "INTERNAL",
            "payload": {"job_id": 99},
        }

        await event_bus.publish(event_dict)
        self.assertEqual(len(dispatched_envelopes), 1)
        self.assertEqual(dispatched_envelopes[0].payload["job_id"], 99)


if __name__ == "__main__":
    unittest.main()
