"""Federated Hermes Handshake & Discovery E2E — Passo 2.

Provides a high-level FederatedOrchestrator integrating FederatedAgentDirectory
and CrossProtocolBridge from hermes.platform.protocols.unified_bus.
Supports discovery of peer agents across nodes, mutual cryptographic handshakes
(HMAC-SHA256 signature exchange and nonce verification), and routing remote
agent tasks via ANP wire envelope with signed perception/task response verification.

Strict stdlib-only; PEP-420 namespace compliant (no __init__.py).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from hermes.platform.capabilities.modality.workers import PerceptionArtifact
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


class HandshakeState(str, Enum):
    """Lifecycle state of a mutual cryptographic handshake."""
    INITIATED = "initiated"
    CHALLENGED = "challenged"
    ESTABLISHED = "established"
    FAILED = "failed"


class HandshakeRole(str, Enum):
    """Role in a mutual handshake session."""
    INITIATOR = "initiator"
    RESPONDER = "responder"


@dataclass
class HandshakeSession:
    """Tracks state and nonces for a mutual cryptographic handshake between nodes."""
    session_id: str
    local_node_id: str
    peer_node_id: str
    role: HandshakeRole
    state: HandshakeState = HandshakeState.INITIATED
    local_nonce: str = field(default_factory=lambda: secrets.token_hex(16))
    peer_nonce: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    established_secret: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RemoteTaskResult:
    """Result of dispatching a task to a remote federated agent."""
    task_id: str
    remote_agent_id: str
    success: bool
    response_payload: Dict[str, Any]
    envelope_id: str
    verified_signature: bool
    perception_artifact: Optional[PerceptionArtifact] = None
    execution_time_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class FederatedOrchestrator:
    """High-level orchestrator for federated multi-node agent discovery and dispatch.

    Features:
    - Node registration and discovery via FederatedAgentDirectory.
    - Mutual cryptographic handshakes (HMAC-SHA256 signature exchange and challenge/response).
    - Remote task routing over ANP (Agent Network Protocol) wire envelopes.
    - Signature verification and PerceptionArtifact extraction from response envelopes.
    """

    def __init__(
        self,
        node_id: str,
        secret_key: Optional[str] = None,
        directory: Optional[FederatedAgentDirectory] = None,
        bridge: Optional[CrossProtocolBridge] = None,
        router: Optional[ProtocolRouter] = None,
    ) -> None:
        self.node_id = node_id
        self.secret_key = secret_key or secrets.token_hex(32)
        
        self.router = router or ProtocolRouter()
        self.directory = directory or FederatedAgentDirectory(router=self.router)
        self.bridge = bridge or CrossProtocolBridge(router=self.router, directory=self.directory)
        
        # Self registration in the protocol router secrets
        self.router.register_shared_secret(self.node_id, self.secret_key)
        
        # In-memory mapping of node/peer shared keys: peer_node_id -> secret_key
        self._node_secrets: Dict[str, str] = {}
        # Active or established handshake sessions: session_id -> HandshakeSession
        self._handshake_sessions: Dict[str, HandshakeSession] = {}
        # Remote peer nodes: peer_node_id -> metadata
        self._peer_nodes: Dict[str, Dict[str, Any]] = {}

    def register_peer_secret(self, peer_node_id: str, shared_secret: str) -> None:
        """Register a pre-shared or out-of-band secret key for a peer node."""
        self._node_secrets[peer_node_id] = shared_secret
        self.router.register_shared_secret(peer_node_id, shared_secret)

    def get_peer_secret(self, peer_node_id: str) -> Optional[str]:
        """Retrieve the secret key associated with a peer node."""
        return self._node_secrets.get(peer_node_id)

    # ========================================================================
    # 1. Peer Discovery via FederatedAgentDirectory
    # ========================================================================

    def register_peer_agent(
        self,
        agent_id: str,
        name: str,
        node_id: str,
        protocol: ProtocolType = ProtocolType.ANP,
        trust_boundary: TrustBoundary = TrustBoundary.FEDERATED,
        capabilities: Optional[List[str]] = None,
        endpoint_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FederatedAgentEndpoint:
        """Register a discovered peer agent belonging to a peer node."""
        meta = dict(metadata or {})
        meta["node_id"] = node_id
        
        secret = self._node_secrets.get(node_id) or self._node_secrets.get(agent_id)

        endpoint = FederatedAgentEndpoint(
            agent_id=agent_id,
            name=name,
            protocol=protocol,
            trust_boundary=trust_boundary,
            capabilities=capabilities or [],
            endpoint_url=endpoint_url or f"anp://{node_id}/agents/{agent_id}",
            secret_key=secret,
            metadata=meta,
        )
        self.directory.register(endpoint)
        
        if node_id not in self._peer_nodes:
            self._peer_nodes[node_id] = {
                "node_id": node_id,
                "first_seen": time.time(),
                "agents": [],
            }
        if agent_id not in self._peer_nodes[node_id]["agents"]:
            self._peer_nodes[node_id]["agents"].append(agent_id)

        return endpoint

    def discover_peer_agents(
        self,
        capability: Optional[str] = None,
        protocol: Optional[ProtocolType] = None,
        node_id: Optional[str] = None,
    ) -> List[FederatedAgentEndpoint]:
        """Discover peer agents filtered by capability, wire protocol, or node_id."""
        if capability:
            candidates = self.directory.find_by_capability(capability)
        elif protocol:
            candidates = self.directory.find_by_protocol(protocol)
        else:
            candidates = self.directory.list_all()

        if node_id is not None:
            candidates = [
                ep for ep in candidates
                if ep.metadata.get("node_id") == node_id
            ]
        return candidates

    def get_agent_endpoint(self, agent_id: str) -> Optional[FederatedAgentEndpoint]:
        """Look up endpoint descriptor for an agent."""
        return self.directory.get(agent_id)

    # ========================================================================
    # 2. Mutual Cryptographic Handshake (HMAC-SHA256)
    # ========================================================================

    def initiate_handshake(
        self,
        peer_node_id: str,
        session_id: Optional[str] = None,
    ) -> ProtocolEnvelope:
        """Initiator step 1: Create a handshake initiation envelope containing local challenge nonce.
        
        Signs the envelope with the shared secret known between local node and peer node.
        """
        shared_secret = self._node_secrets.get(peer_node_id)
        if not shared_secret:
            raise ProtocolSecurityError(f"Cannot initiate handshake: no shared secret for peer '{peer_node_id}'")

        sid = session_id or f"hs-{secrets.token_hex(8)}"
        session = HandshakeSession(
            session_id=sid,
            local_node_id=self.node_id,
            peer_node_id=peer_node_id,
            role=HandshakeRole.INITIATOR,
            state=HandshakeState.INITIATED,
            established_secret=shared_secret,
        )
        self._handshake_sessions[sid] = session

        payload = {
            "handshake_action": "initiate",
            "session_id": sid,
            "initiator_node": self.node_id,
            "nonce": session.local_nonce,
            "timestamp": time.time(),
        }

        envelope = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender=self.node_id,
            recipient=peer_node_id,
            payload=payload,
            trust_boundary=TrustBoundary.FEDERATED,
            metadata={"handshake": True, "session_id": sid},
        )
        envelope.sign(shared_secret)
        return envelope

    def handle_handshake_request(
        self,
        envelope: ProtocolEnvelope,
    ) -> ProtocolEnvelope:
        """Responder step: Verify initiator challenge and generate mutual challenge response.
        
        Calculates proof_of_initiator_nonce = HMAC(secret, initiator_nonce)
        Generates responder_nonce and signs response envelope with shared secret.
        """
        peer_node_id = envelope.sender
        shared_secret = self._node_secrets.get(peer_node_id)
        if not shared_secret:
            raise ProtocolSecurityError(f"Handshake rejected: no shared secret for sender '{peer_node_id}'")

        # Verify envelope signature
        if not envelope.verify_signature(shared_secret):
            raise ProtocolSecurityError(f"Handshake signature verification failed from sender '{peer_node_id}'")

        payload = envelope.payload
        action = payload.get("handshake_action")
        sid = payload.get("session_id")
        initiator_nonce = payload.get("nonce")

        if action != "initiate" or not sid or not initiator_nonce:
            raise ValueError(f"Malformed handshake initiation payload: {payload}")

        session = HandshakeSession(
            session_id=sid,
            local_node_id=self.node_id,
            peer_node_id=peer_node_id,
            role=HandshakeRole.RESPONDER,
            state=HandshakeState.CHALLENGED,
            peer_nonce=initiator_nonce,
            established_secret=shared_secret,
        )
        self._handshake_sessions[sid] = session

        # Responder computes proof of initiator's nonce
        proof = hmac.new(
            shared_secret.encode("utf-8"),
            initiator_nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        response_payload = {
            "handshake_action": "challenge_response",
            "session_id": sid,
            "responder_node": self.node_id,
            "responder_nonce": session.local_nonce,
            "initiator_nonce_proof": proof,
            "timestamp": time.time(),
        }

        response_env = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender=self.node_id,
            recipient=peer_node_id,
            payload=response_payload,
            trust_boundary=TrustBoundary.FEDERATED,
            metadata={"handshake": True, "session_id": sid},
        )
        response_env.sign(shared_secret)
        return response_env

    def complete_handshake(
        self,
        envelope: ProtocolEnvelope,
    ) -> ProtocolEnvelope:
        """Initiator step 2: Verify responder's challenge response and finalize handshake.
        
        Verifies:
        1. Signature of envelope
        2. Proof of initiator nonce
        Generates responder nonce proof and finalizes initiator session to ESTABLISHED.
        """
        peer_node_id = envelope.sender
        shared_secret = self._node_secrets.get(peer_node_id)
        if not shared_secret:
            raise ProtocolSecurityError(f"Handshake complete failed: unknown peer '{peer_node_id}'")

        if not envelope.verify_signature(shared_secret):
            raise ProtocolSecurityError(f"Handshake response signature verification failed from '{peer_node_id}'")

        payload = envelope.payload
        sid = payload.get("session_id")
        session = self._handshake_sessions.get(sid)
        if not session or session.role != HandshakeRole.INITIATOR:
            raise ProtocolSecurityError(f"Invalid or missing handshake session '{sid}'")

        # Verify responder's proof of our local nonce
        expected_proof = hmac.new(
            shared_secret.encode("utf-8"),
            session.local_nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        responder_proof = payload.get("initiator_nonce_proof")
        if not responder_proof or not hmac.compare_digest(responder_proof, expected_proof):
            session.state = HandshakeState.FAILED
            raise ProtocolSecurityError("Mutual handshake failed: invalid initiator nonce proof")

        # Store peer nonce
        session.peer_nonce = payload.get("responder_nonce")
        session.state = HandshakeState.ESTABLISHED
        session.completed_at = time.time()

        # Compute initiator's proof of responder's nonce for the final ACK
        responder_proof_ack = hmac.new(
            shared_secret.encode("utf-8"),
            session.peer_nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        ack_payload = {
            "handshake_action": "finalize_ack",
            "session_id": sid,
            "initiator_node": self.node_id,
            "responder_nonce_proof": responder_proof_ack,
            "timestamp": time.time(),
        }

        ack_envelope = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender=self.node_id,
            recipient=peer_node_id,
            payload=ack_payload,
            trust_boundary=TrustBoundary.FEDERATED,
            metadata={"handshake": True, "session_id": sid, "status": "established"},
        )
        ack_envelope.sign(shared_secret)
        return ack_envelope

    def finalize_responder_handshake(self, ack_envelope: ProtocolEnvelope) -> bool:
        """Responder step 2: Verify initiator's ACK proof of responder nonce and mark ESTABLISHED."""
        peer_node_id = ack_envelope.sender
        shared_secret = self._node_secrets.get(peer_node_id)
        if not shared_secret or not ack_envelope.verify_signature(shared_secret):
            raise ProtocolSecurityError(f"Handshake finalize verification failed from '{peer_node_id}'")

        payload = ack_envelope.payload
        sid = payload.get("session_id")
        session = self._handshake_sessions.get(sid)
        if not session or session.role != HandshakeRole.RESPONDER:
            raise ProtocolSecurityError(f"Invalid responder session '{sid}'")

        expected_proof = hmac.new(
            shared_secret.encode("utf-8"),
            session.local_nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        initiator_proof = payload.get("responder_nonce_proof")
        if not initiator_proof or not hmac.compare_digest(initiator_proof, expected_proof):
            session.state = HandshakeState.FAILED
            raise ProtocolSecurityError("Mutual handshake failed: invalid responder nonce proof from initiator")

        session.state = HandshakeState.ESTABLISHED
        session.completed_at = time.time()
        return True

    def is_handshake_established(self, peer_node_id: str) -> bool:
        """Check if any mutual handshake session with peer_node_id is currently ESTABLISHED."""
        for session in self._handshake_sessions.values():
            if session.peer_node_id == peer_node_id and session.state == HandshakeState.ESTABLISHED:
                return True
        return False

    def perform_mutual_handshake(
        self,
        peer_orchestrator: FederatedOrchestrator,
    ) -> Tuple[HandshakeSession, HandshakeSession]:
        """Execute a complete in-process mutual handshake E2E between two orchestrator nodes.
        
        Returns the established (initiator_session, responder_session).
        """
        # Step 1: Initiator -> Envelope 1
        env1 = self.initiate_handshake(peer_orchestrator.node_id)
        sid = env1.payload["session_id"]
        
        # Step 2: Responder processes Envelope 1 -> Envelope 2 (challenge response)
        env2 = peer_orchestrator.handle_handshake_request(env1)
        
        # Step 3: Initiator verifies Envelope 2 -> Envelope 3 (finalize ACK)
        env3 = self.complete_handshake(env2)
        
        # Step 4: Responder verifies Envelope 3 -> established
        peer_orchestrator.finalize_responder_handshake(env3)

        init_session = self._handshake_sessions[sid]
        resp_session = peer_orchestrator._handshake_sessions[sid]
        return init_session, resp_session

    # ========================================================================
    # 3. Route Remote Agent Tasks (ANP Wire Envelopes & Signed Perception)
    # ========================================================================

    def build_task_envelope(
        self,
        task_id: str,
        target_agent_id: str,
        task_type: str,
        parameters: Dict[str, Any],
        require_mutual_handshake: bool = True,
    ) -> ProtocolEnvelope:
        """Construct a signed ANP wire protocol envelope dispatching a task to a remote agent."""
        endpoint = self.directory.get(target_agent_id)
        if not endpoint:
            raise ProtocolSecurityError(f"Target agent '{target_agent_id}' not found in directory")

        peer_node = endpoint.metadata.get("node_id") or target_agent_id
        if require_mutual_handshake and not self.is_handshake_established(peer_node):
            raise ProtocolSecurityError(
                f"Cannot dispatch task to '{target_agent_id}': mutual handshake with node '{peer_node}' not established"
            )

        secret = endpoint.secret_key or self._node_secrets.get(peer_node)
        if not secret:
            raise ProtocolSecurityError(f"No secret available to sign envelope for agent '{target_agent_id}'")

        envelope_payload = {
            "type": "task_dispatch",
            "task_id": task_id,
            "task_type": task_type,
            "parameters": parameters,
            "created_at": time.time(),
        }

        # Use bridge if converting from internal semantics to ANP
        internal_env = ProtocolEnvelope(
            protocol_type=ProtocolType.INTERNAL,
            sender=self.node_id,
            recipient=target_agent_id,
            payload=envelope_payload,
            trust_boundary=TrustBoundary.FEDERATED,
            metadata={"target_node": peer_node, "task_id": task_id},
        )

        # Build A2A envelope first
        a2a_envelope = self.bridge.internal_to_a2a(
            internal_env,
            recipient=target_agent_id,
        )

        # Convert to ANP wire envelope
        anp_envelope = self.bridge.a2a_to_anp(
            a2a_envelope,
            recipient_did=f"did:wba:{peer_node}:{target_agent_id}",
        )
        if secret:
            anp_envelope.sign(secret)
        return anp_envelope

    def verify_and_process_task_response(
        self,
        response_envelope: ProtocolEnvelope,
        expected_task_id: Optional[str] = None,
    ) -> RemoteTaskResult:
        """Verify the HMAC signature on a remote agent's response envelope and extract outcome/perception."""
        sender_id = response_envelope.sender
        endpoint = self.directory.get(sender_id)
        
        peer_node = endpoint.metadata.get("node_id") if endpoint else sender_id
        secret = (
            (endpoint.secret_key if endpoint else None)
            or self._node_secrets.get(peer_node)
            or self._node_secrets.get(sender_id)
        )

        if not secret:
            raise ProtocolSecurityError(f"No verification secret registered for sender '{sender_id}'")

        if not response_envelope.verify_signature(secret):
            raise ProtocolSecurityError(f"Invalid HMAC signature on task response from '{sender_id}'")

        payload = response_envelope.payload
        # Unwrap ANP body if structured as ANP wire envelope
        if response_envelope.protocol_type == ProtocolType.ANP and "body" in payload:
            inner_body = payload["body"]
            if isinstance(inner_body, dict):
                # Might have nested message/parts or direct payload
                payload = inner_body.get("payload", inner_body)

        task_id = payload.get("task_id", response_envelope.metadata.get("task_id", "unknown"))
        if expected_task_id and task_id != expected_task_id:
            raise ValueError(f"Task ID mismatch: expected '{expected_task_id}', received '{task_id}'")

        success = payload.get("status") in ("success", "completed", "ok") or payload.get("success", False)
        
        # Check for perception artifact
        perception_artifact = None
        artifact_data = payload.get("perception_artifact") or payload.get("perception")
        if isinstance(artifact_data, dict):
            perception_artifact = PerceptionArtifact(
                artifact_id=artifact_data.get("artifact_id", f"art-{task_id}"),
                modality=artifact_data.get("modality", "unknown"),
                summary=artifact_data.get("summary", ""),
                observations=artifact_data.get("observations", []),
                extracted_text=artifact_data.get("extracted_text", ""),
                interpretation=artifact_data.get("interpretation", ""),
                uncertainty=artifact_data.get("uncertainty", "low"),
                structured_data=artifact_data.get("structured_data", {}),
                produced_by=artifact_data.get("produced_by", sender_id),
                model_identity=artifact_data.get("model_identity", {"family": "remote", "variant": "worker"}),
            )

        return RemoteTaskResult(
            task_id=task_id,
            remote_agent_id=sender_id,
            success=success,
            response_payload=payload,
            envelope_id=response_envelope.envelope_id,
            verified_signature=True,
            perception_artifact=perception_artifact,
            execution_time_ms=payload.get("execution_time_ms", 0.0),
            metadata=response_envelope.metadata,
        )

    async def dispatch_task_to_remote_peer(
        self,
        task_id: str,
        target_agent_id: str,
        task_type: str,
        parameters: Dict[str, Any],
        peer_orchestrator: FederatedOrchestrator,
        remote_executor: Optional[Callable[[ProtocolEnvelope], ProtocolEnvelope]] = None,
        require_mutual_handshake: bool = True,
    ) -> RemoteTaskResult:
        """End-to-end task dispatch:
        
        1. Constructs signed ANP envelope.
        2. Routes to peer orchestrator (or remote_executor).
        3. Awaits peer's execution and signed response envelope.
        4. Verifies response envelope signature and extracts RemoteTaskResult.
        """
        # Build signed request
        req_env = self.build_task_envelope(
            task_id=task_id,
            target_agent_id=target_agent_id,
            task_type=task_type,
            parameters=parameters,
            require_mutual_handshake=require_mutual_handshake,
        )

        if remote_executor is not None:
            resp_env = remote_executor(req_env)
        else:
            # Default mock execution on peer orchestrator
            resp_env = peer_orchestrator.execute_incoming_task(req_env)

        # Verify and return structured result
        return self.verify_and_process_task_response(resp_env, expected_task_id=task_id)

    def execute_incoming_task(
        self,
        request_envelope: ProtocolEnvelope,
    ) -> ProtocolEnvelope:
        """Worker-side incoming task processor:
        
        1. Verifies request signature using sender's secret.
        2. Executes task (simulated or modality perception).
        3. Returns HMAC-signed response envelope in ANP wire format.
        """
        sender_id = request_envelope.sender
        secret = self._node_secrets.get(sender_id)
        if not secret:
            raise ProtocolSecurityError(f"No secret registered to process task from sender '{sender_id}'")

        if not request_envelope.verify_signature(secret):
            raise ProtocolSecurityError(f"Incoming task signature verification failed from '{sender_id}'")

        payload = request_envelope.payload
        if request_envelope.protocol_type == ProtocolType.ANP and isinstance(payload, dict):
            # Unwrap ANP body if present
            if "body" in payload:
                body_content = payload["body"]
                if isinstance(body_content, dict) and "parts" in body_content:
                    parts = body_content["parts"]
                    if parts and isinstance(parts, list):
                        first_part = parts[0]
                        if isinstance(first_part, dict) and first_part.get("kind") == "data":
                            payload = first_part.get("data", payload)
                        elif isinstance(first_part, dict) and "data" in first_part:
                            payload = first_part["data"]
                elif isinstance(body_content, dict) and "payload" in body_content:
                    payload = body_content["payload"]
                elif isinstance(body_content, dict):
                    payload = body_content

        task_id = payload.get("task_id", "unknown")
        task_type = payload.get("task_type", "generic")
        params = payload.get("parameters", {})

        # Default simulated execution: if task_type indicates modality or analysis, create PerceptionArtifact
        response_data: Dict[str, Any] = {
            "task_id": task_id,
            "status": "success",
            "executed_by": self.node_id,
            "timestamp": time.time(),
        }

        if task_type in ("multimodal_analysis", "perception", "vision"):
            artifact = PerceptionArtifact(
                artifact_id=f"art-{task_id}",
                modality=params.get("modality", "vision"),
                summary=f"Analysis of {params.get('target', 'asset')} completed successfully.",
                observations=[
                    f"Observation 1 for {params.get('target', 'asset')}",
                    "Verified federated signatures and wire format",
                ],
                extracted_text=params.get("input_text", "ANP 1.1 DID Identity"),
                interpretation="Peer worker verified context integrity across trust boundary.",
                uncertainty="low",
                structured_data={"result_code": 200, "node": self.node_id},
                produced_by=self.node_id,
                model_identity={"family": "vision", "variant": "peer-primary"},
            )
            response_data["perception_artifact"] = {
                "artifact_id": artifact.artifact_id,
                "modality": artifact.modality,
                "summary": artifact.summary,
                "observations": artifact.observations,
                "extracted_text": artifact.extracted_text,
                "interpretation": artifact.interpretation,
                "uncertainty": artifact.uncertainty,
                "structured_data": artifact.structured_data,
                "produced_by": artifact.produced_by,
                "model_identity": artifact.model_identity,
            }
        else:
            response_data["result"] = {"status": "ok", "task_id": task_id, "params": params}

        response_env = ProtocolEnvelope(
            protocol_type=ProtocolType.ANP,
            sender=self.node_id,
            recipient=sender_id,
            payload={"body": {"payload": response_data}},
            trust_boundary=TrustBoundary.FEDERATED,
            metadata={"task_id": task_id, "reply_to": request_envelope.envelope_id},
        )
        response_env.sign(secret)
        return response_env
