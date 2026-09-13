"""Unified Protocol Fabric — Marco 7.

Unified protocol bus mapping Internal Bus, MCP, ACP, A2A, and ANP.
Dispatches cross-agent and cross-system messages across verified trust boundaries.

Strict stdlib-only; PEP-420 namespace compliant (no __init__.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple, Union


__all__ = [
    "ProtocolType",
    "TrustBoundary",
    "ProtocolSecurityError",
    "ProtocolRoutingError",
    "ProtocolEnvelope",
    "RouteRegistration",
    "ProtocolRouter",
    "FederatedAgentEndpoint",
    "FederatedAgentDirectory",
    "CrossProtocolBridge",
]


class ProtocolType(str, Enum):
    """Supported protocol wire standards."""
    INTERNAL = "INTERNAL"
    MCP = "MCP"
    ACP = "ACP"
    A2A = "A2A"
    ANP = "ANP"


class TrustBoundary(str, Enum):
    """Boundary classifications for trust separation."""
    KERNEL = "kernel"             # In-process sovereign kernel (highest trust)
    LOCAL_SECURE = "local_secure" # Same machine, verified IPC/socket/process
    AGENT_SANDBOX = "agent_sandbox" # Isolated agent execution environment
    FEDERATED = "federated"       # ANP/A2A remote verified peers
    UNTRUSTED = "untrusted"       # External untrusted caller or open internet


class ProtocolSecurityError(Exception):
    """Raised when trust boundary or cryptographic verification fails."""
    pass


class ProtocolRoutingError(Exception):
    """Raised when no route exists or delivery fails."""
    pass


@dataclass
class ProtocolEnvelope:
    """Canonical cross-boundary protocol message envelope.

    Encapsulates messages across INTERNAL, MCP, ACP, A2A, and ANP protocols.
    """
    protocol_type: ProtocolType
    sender: str
    recipient: str
    payload: Dict[str, Any]
    trust_boundary: TrustBoundary = TrustBoundary.LOCAL_SECURE
    timestamp: float = field(default_factory=time.time)
    signature: Optional[str] = None
    envelope_id: str = field(default_factory=lambda: hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16])
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.protocol_type, str):
            self.protocol_type = ProtocolType(self.protocol_type)
        if isinstance(self.trust_boundary, str):
            self.trust_boundary = TrustBoundary(self.trust_boundary)

    def canonical_digest(self) -> str:
        """Calculate canonical SHA256 digest of envelope contents (excluding signature)."""
        content = {
            "protocol_type": self.protocol_type.value,
            "sender": self.sender,
            "recipient": self.recipient,
            "payload": self.payload,
            "trust_boundary": self.trust_boundary.value,
            "timestamp": round(self.timestamp, 4),
            "envelope_id": self.envelope_id,
            "metadata": self.metadata,
        }
        serialized = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def sign(self, secret_key: Union[str, bytes]) -> None:
        """Sign this envelope using HMAC-SHA256."""
        if isinstance(secret_key, str):
            secret_key = secret_key.encode("utf-8")
        digest = self.canonical_digest().encode("utf-8")
        self.signature = hmac.new(secret_key, digest, hashlib.sha256).hexdigest()

    def verify_signature(self, secret_key: Union[str, bytes]) -> bool:
        """Verify the signature using HMAC-SHA256."""
        if not self.signature:
            return False
        if isinstance(secret_key, str):
            secret_key = secret_key.encode("utf-8")
        digest = self.canonical_digest().encode("utf-8")
        expected = hmac.new(secret_key, digest, hashlib.sha256).hexdigest()
        return hmac.compare_digest(self.signature, expected)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize envelope to JSON-compatible dictionary."""
        return {
            "protocol_type": self.protocol_type.value,
            "sender": self.sender,
            "recipient": self.recipient,
            "payload": self.payload,
            "trust_boundary": self.trust_boundary.value,
            "timestamp": self.timestamp,
            "signature": self.signature,
            "envelope_id": self.envelope_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProtocolEnvelope:
        """Deserialize envelope from dictionary."""
        return cls(
            protocol_type=ProtocolType(data["protocol_type"]),
            sender=data["sender"],
            recipient=data["recipient"],
            payload=data["payload"],
            trust_boundary=TrustBoundary(data.get("trust_boundary", TrustBoundary.LOCAL_SECURE.value)),
            timestamp=data.get("timestamp", time.time()),
            signature=data.get("signature"),
            envelope_id=data.get("envelope_id", hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16]),
            metadata=data.get("metadata", {}),
        )


HandlerFunc = Callable[[ProtocolEnvelope], Union[None, Coroutine[Any, Any, Any]]]


@dataclass
class RouteRegistration:
    """Registered route handler with trust policy."""
    recipient_pattern: str
    handler: HandlerFunc
    allowed_protocols: Set[ProtocolType]
    minimum_trust: TrustBoundary
    require_signature: bool = False


class ProtocolRouter:
    """Unified Protocol Router for cross-agent and cross-system dispatch.

    Maps Internal Bus, MCP, ACP, A2A, and ANP protocols, verifying boundary
    rules, signatures, and routing across agents and components.
    """

    # Hierarchy order of trust boundaries (index = trust level)
    _TRUST_HIERARCHY: List[TrustBoundary] = [
        TrustBoundary.UNTRUSTED,
        TrustBoundary.FEDERATED,
        TrustBoundary.AGENT_SANDBOX,
        TrustBoundary.LOCAL_SECURE,
        TrustBoundary.KERNEL,
    ]

    def __init__(self, shared_secrets: Optional[Dict[str, str]] = None) -> None:
        self._routes: List[RouteRegistration] = []
        # sender_id -> secret key for verification
        self._shared_secrets: Dict[str, str] = shared_secrets or {}
        self._dispatch_log: List[ProtocolEnvelope] = []

    def register_secret(self, entity_id: str, secret: str) -> None:
        """Register shared secret key for signature verification of entity."""
        self._shared_secrets[entity_id] = secret

    def register_shared_secret(self, entity_id: str, secret: str) -> None:
        """Alias for register_secret."""
        self.register_secret(entity_id, secret)

    def register_route(
        self,
        recipient_pattern: str,
        handler: HandlerFunc,
        allowed_protocols: Optional[Iterable[ProtocolType]] = None,
        minimum_trust: TrustBoundary = TrustBoundary.UNTRUSTED,
        require_signature: bool = False,
        min_trust_boundary: Optional[TrustBoundary] = None,
    ) -> None:
        """Register a destination handler with trust and protocol boundaries."""
        min_trust = min_trust_boundary if min_trust_boundary is not None else minimum_trust
        protocols = set(allowed_protocols) if allowed_protocols else set(ProtocolType)
        self._routes.append(
            RouteRegistration(
                recipient_pattern=recipient_pattern,
                handler=handler,
                allowed_protocols=protocols,
                minimum_trust=min_trust,
                require_signature=require_signature,
            )
        )

    def _is_trust_sufficient(self, incoming: TrustBoundary, required: TrustBoundary) -> bool:
        incoming_level = self._TRUST_HIERARCHY.index(incoming)
        required_level = self._TRUST_HIERARCHY.index(required)
        return incoming_level >= required_level

    def _match_recipient(self, pattern: str, recipient: str) -> bool:
        if pattern == "*" or pattern == recipient:
            return True
        if pattern.endswith("/*"):
            prefix = pattern[:-2]
            return recipient.startswith(prefix + "/")
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return recipient.startswith(prefix)
        return False

    async def dispatch(self, envelope: ProtocolEnvelope) -> List[Any]:
        """Dispatch a protocol envelope through verified trust boundaries.

        Validates:
        1. Protocol compatibility
        2. Trust boundary level
        3. Signature (if required)
        """
        matched_routes = [
            route for route in self._routes
            if self._match_recipient(route.recipient_pattern, envelope.recipient)
        ]

        if not matched_routes:
            raise ProtocolRoutingError(
                f"No route registered for recipient '{envelope.recipient}' under protocol {envelope.protocol_type.value}"
            )

        results = []
        for route in matched_routes:
            # 1. Protocol check
            if envelope.protocol_type not in route.allowed_protocols:
                raise ProtocolSecurityError(
                    f"Protocol '{envelope.protocol_type.value}' not allowed for route '{route.recipient_pattern}'. "
                    f"Allowed: {[p.value for p in route.allowed_protocols]}"
                )

            # 2. Trust boundary check
            if not self._is_trust_sufficient(envelope.trust_boundary, route.minimum_trust):
                raise ProtocolSecurityError(
                    f"Insufficient trust boundary '{envelope.trust_boundary.value}' for recipient "
                    f"'{envelope.recipient}' (minimum required: '{route.minimum_trust.value}')"
                )

            # 3. Signature verification check
            if route.require_signature:
                if not envelope.signature:
                    raise ProtocolSecurityError(
                        f"Route '{route.recipient_pattern}' strictly requires signature, but none provided."
                    )
                secret = self._shared_secrets.get(envelope.sender)
                if not secret:
                    raise ProtocolSecurityError(
                        f"No secret registered to verify signature from sender '{envelope.sender}'"
                    )
                if not envelope.verify_signature(secret):
                    raise ProtocolSecurityError(
                        f"Invalid signature from sender '{envelope.sender}' on envelope {envelope.envelope_id}"
                    )
            elif envelope.trust_boundary in (TrustBoundary.FEDERATED, TrustBoundary.UNTRUSTED):
                if not envelope.signature:
                    raise ProtocolSecurityError(
                        f"Signature strictly required for {envelope.trust_boundary.value} message or route '{route.recipient_pattern}', but none provided."
                    )
                secret = self._shared_secrets.get(envelope.sender)
                if not secret:
                    raise ProtocolSecurityError(
                        f"No secret registered to verify signature from sender '{envelope.sender}'"
                    )
                if not envelope.verify_signature(secret):
                    raise ProtocolSecurityError(
                        f"Invalid signature from sender '{envelope.sender}' on envelope {envelope.envelope_id}"
                    )
            elif envelope.signature:
                # Se uma assinatura foi fornecida, verifica
                secret = self._shared_secrets.get(envelope.sender)
                if secret and not envelope.verify_signature(secret):
                    raise ProtocolSecurityError(
                        f"Invalid signature from sender '{envelope.sender}' on envelope {envelope.envelope_id}"
                    )

            # Execute handler
            res = route.handler(envelope)
            if asyncio.iscoroutine(res):
                res = await res
            results.append(res)

        self._dispatch_log.append(envelope)
        return results

    def dispatch_sync(self, envelope: ProtocolEnvelope) -> List[Any]:
        """Synchronous wrapper around dispatch for synchronous environments."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.dispatch(envelope))
        else:
            # Running inside an active event loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(asyncio.run, self.dispatch(envelope)).result()


# ============================================================================
# Federated Agent Directory
# ============================================================================

@dataclass
class FederatedAgentEndpoint:
    """Descriptor for a registered federated agent endpoint."""
    agent_id: str
    name: str
    protocol: ProtocolType
    trust_boundary: TrustBoundary
    endpoint_url: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    secret_key: Optional[str] = None


class FederatedAgentDirectory:
    """Registry and directory service for discovering and locating federated agents."""

    def __init__(self, router: Optional[ProtocolRouter] = None) -> None:
        self._endpoints: Dict[str, FederatedAgentEndpoint] = {}
        self._router: Optional[ProtocolRouter] = router

    def attach_router(self, router: ProtocolRouter) -> None:
        self._router = router

    def register(self, endpoint: FederatedAgentEndpoint) -> None:
        """Register an agent endpoint and sync secret with router if available."""
        self._endpoints[endpoint.agent_id] = endpoint
        if self._router and endpoint.secret_key:
            self._router.register_secret(endpoint.agent_id, endpoint.secret_key)

    def unregister(self, agent_id: str) -> Optional[FederatedAgentEndpoint]:
        """Unregister an agent endpoint."""
        return self._endpoints.pop(agent_id, None)

    def get(self, agent_id: str) -> Optional[FederatedAgentEndpoint]:
        """Retrieve endpoint by agent id."""
        return self._endpoints.get(agent_id)

    def find_by_capability(self, capability: str) -> List[FederatedAgentEndpoint]:
        """Find all agents offering a specific capability."""
        cap_lower = capability.lower()
        return [
            ep for ep in self._endpoints.values()
            if any(cap_lower == c.lower() or cap_lower in c.lower() for c in ep.capabilities)
        ]

    def find_by_protocol(self, protocol: ProtocolType) -> List[FederatedAgentEndpoint]:
        """Find all agents supporting a specific wire protocol."""
        return [ep for ep in self._endpoints.values() if ep.protocol == protocol]

    def list_all(self) -> List[FederatedAgentEndpoint]:
        """Return all registered agent endpoints."""
        return list(self._endpoints.values())


# ============================================================================
# CrossProtocolBridge
# ============================================================================

class CrossProtocolBridge:
    """Translates seamlessly between protocol shapes:
    e.g. ACP client event -> INTERNAL event bus -> A2A message -> ANP wire envelope.
    """

    def __init__(
        self,
        router: Optional[ProtocolRouter] = None,
        directory: Optional[FederatedAgentDirectory] = None,
    ) -> None:
        self.router = router
        self.directory = directory

    @staticmethod
    def acp_to_internal(
        acp_event: Dict[str, Any],
        sender: str = "acp_client",
        recipient: str = "kernel/event_bus",
        trust_boundary: TrustBoundary = TrustBoundary.LOCAL_SECURE,
    ) -> ProtocolEnvelope:
        """Translate ACP client event / notification into INTERNAL ProtocolEnvelope."""
        # ACP notifications typically have {"jsonrpc": "2.0", "method": "...", "params": {...}}
        # or session client events {"type": "event", ...}
        method = acp_event.get("method") or acp_event.get("type", "acp.event")
        payload = acp_event.get("params") or acp_event.get("payload") or acp_event
        metadata = {
            "source_protocol": ProtocolType.ACP.value,
            "method": method,
            "raw_id": acp_event.get("id"),
        }
        return ProtocolEnvelope(
            protocol_type=ProtocolType.INTERNAL,
            sender=sender,
            recipient=recipient,
            payload=payload,
            trust_boundary=trust_boundary,
            metadata=metadata,
        )

    @staticmethod
    def internal_to_a2a(
        envelope: ProtocolEnvelope,
        recipient: Optional[str] = None,
        role: str = "user",
        context_id: Optional[str] = None,
    ) -> ProtocolEnvelope:
        """Translate INTERNAL ProtocolEnvelope into an A2A message ProtocolEnvelope."""
        target_recipient = recipient or envelope.recipient
        payload_data = envelope.payload
        
        # Build A2A parts structure
        parts: List[Dict[str, Any]] = []
        if isinstance(payload_data, dict):
            if "parts" in payload_data and isinstance(payload_data["parts"], list):
                parts = payload_data["parts"]
            elif "text" in payload_data:
                parts = [{"kind": "text", "text": str(payload_data["text"])}]
            elif "content" in payload_data:
                parts = [{"kind": "text", "text": str(payload_data["content"])}]
            else:
                parts = [{"kind": "data", "data": payload_data}]
        elif isinstance(payload_data, str):
            parts = [{"kind": "text", "text": payload_data}]
        else:
            parts = [{"kind": "data", "data": payload_data}]

        a2a_payload = {
            "role": role,
            "parts": parts,
            "kind": "message",
            "message_id": envelope.envelope_id,
            "context_id": context_id or envelope.metadata.get("context_id"),
        }
        
        metadata = dict(envelope.metadata)
        metadata["source_protocol"] = envelope.protocol_type.value
        metadata["translated_via"] = "CrossProtocolBridge.internal_to_a2a"

        return ProtocolEnvelope(
            protocol_type=ProtocolType.A2A,
            sender=envelope.sender,
            recipient=target_recipient,
            payload=a2a_payload,
            trust_boundary=envelope.trust_boundary,
            metadata=metadata,
        )

    @staticmethod
    def a2a_to_anp(
        envelope: ProtocolEnvelope,
        recipient_did: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> ProtocolEnvelope:
        """Translate A2A ProtocolEnvelope into an ANP wire envelope."""
        target_did = recipient_did or envelope.recipient
        if not target_did.startswith("did:"):
            # Format as DID if plain name
            dom = domain or "local.hermes"
            target_did = f"did:wba:{dom}:{target_did.replace('/', ':')}"

        a2a_body = envelope.payload
        anp_payload = {
            "meta": {
                "profile": "anp.messaging.v1",
                "message_id": envelope.envelope_id,
                "timestamp": envelope.timestamp,
                "reply_to": envelope.sender,
            },
            "body": a2a_body,
            "target_did": target_did,
        }

        metadata = dict(envelope.metadata)
        metadata["source_protocol"] = envelope.protocol_type.value
        metadata["translated_via"] = "CrossProtocolBridge.a2a_to_anp"

        return ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender=envelope.sender,
            recipient=target_did,
            payload=anp_payload,
            trust_boundary=envelope.trust_boundary,
            metadata=metadata,
        )

    @staticmethod
    def anp_to_internal(
        envelope: ProtocolEnvelope,
        recipient: str = "kernel/event_bus",
    ) -> ProtocolEnvelope:
        """Translate ANP wire envelope back to INTERNAL event bus envelope."""
        payload = envelope.payload
        body = payload.get("body", payload) if isinstance(payload, dict) else payload
        metadata = dict(envelope.metadata)
        metadata["source_protocol"] = ProtocolType.ANP.value
        metadata["original_sender_did"] = envelope.sender

        return ProtocolEnvelope(
            protocol_type=ProtocolType.INTERNAL,
            sender=envelope.sender,
            recipient=recipient,
            payload=body,
            trust_boundary=envelope.trust_boundary,
            metadata=metadata,
        )

    async def bridge_pipeline(
        self,
        source_envelope: ProtocolEnvelope,
        target_protocol: ProtocolType,
        target_recipient: Optional[str] = None,
        secret_key: Optional[str] = None,
    ) -> ProtocolEnvelope:
        """Pipeline that progressively transforms an envelope from source protocol to target protocol.
        Supports seamless transition across: ACP -> INTERNAL -> A2A -> ANP (and reverse).
        """
        current = source_envelope

        # If starting from ACP, translate to INTERNAL first
        if current.protocol_type == ProtocolType.ACP and target_protocol != ProtocolType.ACP:
            current = self.acp_to_internal(
                current.payload if isinstance(current.payload, dict) else {"payload": current.payload},
                sender=current.sender,
                recipient=target_recipient or "internal/bus",
                trust_boundary=current.trust_boundary,
            )

        # If target is INTERNAL, we are done if already INTERNAL
        if target_protocol == ProtocolType.INTERNAL:
            if current.protocol_type == ProtocolType.ANP:
                current = self.anp_to_internal(current, recipient=target_recipient or "kernel/event_bus")
            elif current.protocol_type != ProtocolType.INTERNAL:
                current = ProtocolEnvelope(
                    protocol_type=ProtocolType.INTERNAL,
                    sender=current.sender,
                    recipient=target_recipient or "kernel/event_bus",
                    payload=current.payload,
                    trust_boundary=current.trust_boundary,
                    metadata=current.metadata,
                )
            if secret_key:
                current.sign(secret_key)
            return current

        # If moving to A2A or ANP, ensure we pass through A2A
        if current.protocol_type == ProtocolType.INTERNAL and target_protocol in (ProtocolType.A2A, ProtocolType.ANP):
            current = self.internal_to_a2a(current, recipient=target_recipient)

        # If target is A2A, we are done
        if target_protocol == ProtocolType.A2A:
            if secret_key:
                current.sign(secret_key)
            return current

        # If target is ANP
        if target_protocol == ProtocolType.ANP:
            if current.protocol_type != ProtocolType.A2A:
                current = self.internal_to_a2a(current, recipient=target_recipient)
            current = self.a2a_to_anp(current, recipient_did=target_recipient)
            if secret_key:
                current.sign(secret_key)
            return current

        # Fallback direct envelope reconstruction
        result = ProtocolEnvelope(
            protocol_type=target_protocol,
            sender=current.sender,
            recipient=target_recipient or current.recipient,
            payload=current.payload,
            trust_boundary=current.trust_boundary,
            metadata=current.metadata,
        )
        if secret_key:
            result.sign(secret_key)
        return result
