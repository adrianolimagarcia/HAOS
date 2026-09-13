"""Tests for UniversalProtocolGateway unifying A2A, MCP, and external protocols."""

from __future__ import annotations

import pytest
from hermes.platform.capabilities.universal_registry import UniversalCapabilityRegistry
from hermes.platform.mcp.aggregator import LocalMCPAggregator
from hermes.platform.observability.event_store import EventStore
from hermes.platform.protocols.gateway import (
    AgentCard,
    FederatedCapabilityResolver,
    RemoteAgentReputationTracker,
    UniversalProtocolGateway,
)
from hermes.platform.protocols.unified_bus import (
    ProtocolEnvelope,
    ProtocolType,
    TrustBoundary,
)


@pytest.fixture
def mock_gateway_setup():
    event_store = EventStore()
    registry = UniversalCapabilityRegistry()
    reputation = RemoteAgentReputationTracker()
    resolver = FederatedCapabilityResolver(local_registry=registry, reputation_tracker=reputation)
    mcp_aggregator = LocalMCPAggregator()

    # Pre-register a tool in aggregator
    mcp_aggregator.register_server_tools(
        "calculator",
        [
            {
                "name": "add",
                "description": "Add two numbers",
                "inputSchema": {
                    "type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                },
            }
        ],
    )

    async def calc_dispatcher(tool_name: str, args: dict) -> dict:
        if tool_name == "add":
            return {"result": args.get("a", 0) + args.get("b", 0)}
        return {"error": "unknown tool"}

    mcp_aggregator.register_dispatcher("calculator", calc_dispatcher)

    gateway = UniversalProtocolGateway(
        event_store=event_store,
        capability_resolver=resolver,
        mcp_aggregator=mcp_aggregator,
    )
    return gateway, event_store, mcp_aggregator


def test_unified_gateway_mcp_tools_list(mock_gateway_setup):
    gateway, event_store, _ = mock_gateway_setup

    envelope = ProtocolEnvelope(
        protocol_type=ProtocolType.MCP,
        sender="agent:hermes",
        recipient="gateway:mcp",
        payload={"id": "1", "method": "tools/list", "params": {}},
        trust_boundary=TrustBoundary.KERNEL,
    )

    response = gateway.dispatch(envelope)

    assert response.protocol_type == ProtocolType.MCP
    assert response.payload["jsonrpc"] == "2.0"
    tools = response.payload["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "calculator_add"


def test_unified_gateway_mcp_tool_call(mock_gateway_setup):
    gateway, event_store, _ = mock_gateway_setup

    envelope = ProtocolEnvelope(
        protocol_type=ProtocolType.MCP,
        sender="agent:hermes",
        recipient="gateway:mcp",
        payload={
            "id": "2",
            "method": "tools/call",
            "params": {"name": "calculator_add", "arguments": {"a": 10, "b": 25}},
        },
        trust_boundary=TrustBoundary.KERNEL,
    )

    response = gateway.dispatch(envelope)

    assert response.protocol_type == ProtocolType.MCP
    assert response.payload["result"]["result"] == 35

    # Check telemetry events recorded
    events = [e.name for e in event_store.get_all()]
    assert "gateway.envelope_dispatched" in events
    assert "gateway.envelope_processed" in events


def test_unified_gateway_a2a_dispatch(mock_gateway_setup):
    gateway, event_store, _ = mock_gateway_setup

    # Register A2A handler
    def handle_a2a(env: ProtocolEnvelope) -> ProtocolEnvelope:
        return ProtocolEnvelope(
            protocol_type=ProtocolType.A2A,
            sender="peer:agent_b",
            recipient=env.sender,
            payload={"status": "task_received", "task_id": env.payload.get("task_id")},
            trust_boundary=TrustBoundary.FEDERATED,
        )

    gateway.register_protocol_handler(ProtocolType.A2A, handle_a2a)

    envelope = ProtocolEnvelope(
        protocol_type=ProtocolType.A2A,
        sender="agent:hermes",
        recipient="peer:agent_b",
        payload={"task_id": "TASK-123", "goal": "Generate report"},
        trust_boundary=TrustBoundary.KERNEL,
    )

    response = gateway.dispatch(envelope)
    assert response.protocol_type == ProtocolType.A2A
    assert response.payload["status"] == "task_received"
    assert response.payload["task_id"] == "TASK-123"
