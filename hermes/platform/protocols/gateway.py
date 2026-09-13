"""Protocol Gateway & Federation Runtime (Phase 4 — Protocol & Federation).

Implements:
1. AgentCard: Typed identity, metadata, protocol type and advertised capabilities.
2. FederatedCapabilityResolver: Multi-tiered resolution (Local -> ANP -> A2A) with proxy creation.
3. RemoteAgentReputationTracker: Local reputation, provenance and trust-tier enforcement.
4. UniversalProtocolGateway: Unifies ANP, A2A, ACP, and federated MCP routing under a single dispatch interface.
"""

from __future__ import annotations

import collections
import dataclasses
import enum
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from hermes.platform.capabilities.universal_registry import UniversalCapabilityRegistry
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.protocols.unified_bus import ProtocolEnvelope, ProtocolType, TrustBoundary


@dataclass
class AgentCard:
    """Public advertisement of an agent's identity, capabilities, and trust parameters."""
    agent_id: str
    name: str
    description: str
    protocol: ProtocolType
    endpoint_url: str
    capabilities: List[str] = field(default_factory=list)
    trust_boundary: TrustBoundary = TrustBoundary.FEDERATED
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RemoteAgentReputation:
    agent_id: str
    total_delegations: int = 0
    successful_completions: int = 0
    tampering_violations: int = 0
    total_latency_ms: float = 0.0

    @property
    def score(self) -> float:
        if self.tampering_violations > 0:
            return 0.0
        if self.total_delegations == 0:
            return 0.50  # Neutral prior
        return (self.successful_completions + 1) / (self.total_delegations + 2)


class RemoteAgentReputationTracker:
    """Tracks performance, provenance, and reputation of external federated agents."""

    def __init__(self, quarantine_threshold: float = 0.25):
        self.quarantine_threshold = quarantine_threshold
        self._reputations: Dict[str, RemoteAgentReputation] = {}

    def record_success(self, agent_id: str, latency_ms: float) -> None:
        rep = self._get_or_create(agent_id)
        rep.total_delegations += 1
        rep.successful_completions += 1
        rep.total_latency_ms += latency_ms

    def record_failure(self, agent_id: str, latency_ms: float) -> None:
        rep = self._get_or_create(agent_id)
        rep.total_delegations += 1
        rep.total_latency_ms += latency_ms

    def record_tampering(self, agent_id: str) -> None:
        rep = self._get_or_create(agent_id)
        rep.total_delegations += 1
        rep.tampering_violations += 1

    def is_quarantined(self, agent_id: str) -> bool:
        rep = self._get_or_create(agent_id)
        return rep.score < self.quarantine_threshold

    def get_score(self, agent_id: str) -> float:
        return self._get_or_create(agent_id).score

    def _get_or_create(self, agent_id: str) -> RemoteAgentReputation:
        if agent_id not in self._reputations:
            self._reputations[agent_id] = RemoteAgentReputation(agent_id=agent_id)
        return self._reputations[agent_id]


@dataclass
class FederatedResolutionResult:
    capability_id: str
    resolved_locally: bool
    provider_agent: Optional[AgentCard] = None
    protocol: Optional[ProtocolType] = None
    proxy_handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None


class FederatedCapabilityResolver:
    """Multi-tiered resolver: Local Registry -> ANP Mesh Directory -> A2A External Directory."""

    def __init__(
        self,
        local_registry: UniversalCapabilityRegistry,
        reputation_tracker: Optional[RemoteAgentReputationTracker] = None,
    ):
        self.local_registry = local_registry
        self.reputation = reputation_tracker or RemoteAgentReputationTracker()
        self._directory: Dict[str, AgentCard] = {}  # agent_id -> AgentCard

    def register_remote_agent(self, card: AgentCard) -> None:
        self._directory[card.agent_id] = card

    def resolve_capability(
        self,
        capability_id: str,
        remote_dispatch_fn: Optional[Callable[[AgentCard, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> FederatedResolutionResult:
        # Tier 1: Check Local Capability Registry
        local_cap = self.local_registry.get(capability_id)
        if local_cap is not None:
            return FederatedResolutionResult(
                capability_id=capability_id,
                resolved_locally=True,
                proxy_handler=None,
            )

        # Tier 2: Search Remote Agent Cards (ANP & A2A)
        candidate_agents: List[AgentCard] = [
            agent for agent in self._directory.values()
            if capability_id in agent.capabilities and not self.reputation.is_quarantined(agent.agent_id)
        ]

        if not candidate_agents:
            raise KeyError(f"Capability '{capability_id}' could not be resolved locally or across federated network.")

        # Pick candidate with highest reputation score
        candidate_agents.sort(key=lambda a: self.reputation.get_score(a.agent_id), reverse=True)
        chosen_agent = candidate_agents[0]

        # Build sandboxed proxy handler
        def sandboxed_proxy_handler(params: Dict[str, Any]) -> Dict[str, Any]:
            if remote_dispatch_fn:
                t0 = time.time()
                try:
                    result = remote_dispatch_fn(chosen_agent, params)
                    self.reputation.record_success(chosen_agent.agent_id, (time.time() - t0) * 1000)
                    return result
                except Exception as exc:
                    self.reputation.record_failure(chosen_agent.agent_id, (time.time() - t0) * 1000)
                    raise
            return {"status": "proxied", "target_agent": chosen_agent.agent_id, "payload": params}

        return FederatedResolutionResult(
            capability_id=capability_id,
            resolved_locally=False,
            provider_agent=chosen_agent,
            protocol=chosen_agent.protocol,
            proxy_handler=sandboxed_proxy_handler,
        )


class UniversalProtocolGateway:
    """Unifies routing across ANP (Web), A2A (Agents), ACP (IDEs), and federated MCP (Tools)."""

    def __init__(
        self,
        event_store: EventStore,
        capability_resolver: FederatedCapabilityResolver,
        mcp_aggregator: Optional[Any] = None,
    ):
        self.event_store = event_store
        self.capability_resolver = capability_resolver
        self._mcp_aggregator = mcp_aggregator
        self._handlers: Dict[ProtocolType, Callable[[ProtocolEnvelope], ProtocolEnvelope]] = {}

        # Default handler for MCP using aggregator if provided
        if self._mcp_aggregator is not None:
            self._handlers[ProtocolType.MCP] = self._handle_mcp_envelope

    @property
    def mcp_aggregator(self) -> Optional[Any]:
        if self._mcp_aggregator is None:
            from hermes.platform.mcp.aggregator import get_local_aggregator
            self._mcp_aggregator = get_local_aggregator()
        return self._mcp_aggregator

    def register_protocol_handler(
        self,
        protocol: ProtocolType,
        handler: Callable[[ProtocolEnvelope], ProtocolEnvelope],
    ) -> None:
        self._handlers[protocol] = handler

    def _handle_mcp_envelope(self, envelope: ProtocolEnvelope) -> ProtocolEnvelope:
        """Handles MCP JSON-RPC protocol messages over the unified fabric."""
        import asyncio
        payload = envelope.payload or {}
        method = payload.get("method", "")
        params = payload.get("params", {})
        request_id = payload.get("id", str(uuid.uuid4())[:8])

        aggregator = self.mcp_aggregator

        if method == "tools/list":
            tools = aggregator.list_tools() if aggregator else []
            return ProtocolEnvelope(
                protocol_type=ProtocolType.MCP,
                sender="gateway:mcp",
                recipient=envelope.sender,
                payload={"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}},
                trust_boundary=TrustBoundary.LOCAL_SECURE,
            )
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        call_res = pool.submit(
                            lambda: asyncio.run(aggregator.call_tool(tool_name, tool_args))
                        ).result()
                else:
                    call_res = asyncio.run(aggregator.call_tool(tool_name, tool_args))

                return ProtocolEnvelope(
                    protocol_type=ProtocolType.MCP,
                    sender="gateway:mcp",
                    recipient=envelope.sender,
                    payload={"jsonrpc": "2.0", "id": request_id, "result": call_res},
                    trust_boundary=TrustBoundary.LOCAL_SECURE,
                )
            except Exception as exc:
                return ProtocolEnvelope(
                    protocol_type=ProtocolType.MCP,
                    sender="gateway:mcp",
                    recipient=envelope.sender,
                    payload={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": str(exc)},
                    },
                    trust_boundary=TrustBoundary.LOCAL_SECURE,
                )
        else:
            return ProtocolEnvelope(
                protocol_type=ProtocolType.MCP,
                sender="gateway:mcp",
                recipient=envelope.sender,
                payload={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method '{method}' not supported in MCP gateway handler."},
                },
                trust_boundary=TrustBoundary.LOCAL_SECURE,
            )

    def dispatch(self, envelope: ProtocolEnvelope) -> ProtocolEnvelope:
        """Routes an envelope through the appropriate protocol handler with telemetry."""
        # Ensure MCP default handler is lazily active if requested
        if envelope.protocol_type == ProtocolType.MCP and ProtocolType.MCP not in self._handlers:
            self._handlers[ProtocolType.MCP] = self._handle_mcp_envelope

        handler = self._handlers.get(envelope.protocol_type)
        if not handler:
            raise ValueError(f"No registered handler for protocol {envelope.protocol_type}")

        self.event_store.append(
            Event(
                name="gateway.envelope_dispatched",
                payload={
                    "envelope_id": envelope.envelope_id,
                    "protocol": envelope.protocol_type.value,
                    "sender": envelope.sender,
                    "recipient": envelope.recipient,
                },
            )
        )

        response = handler(envelope)

        self.event_store.append(
            Event(
                name="gateway.envelope_processed",
                payload={
                    "envelope_id": response.envelope_id,
                    "protocol": response.protocol_type.value,
                    "status": "success",
                },
            )
        )

        return response
