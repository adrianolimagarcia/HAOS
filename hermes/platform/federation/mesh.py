"""Federated Node Live Setup & Mesh — Passo 2.

Provides live networking / socket mesh infrastructure for federated Hermes nodes.
Supports:
- Node communication via socket / HTTP JSON-RPC envelope exchange
- 3-way mutual HMAC-SHA256 handshake over the wire
- Remote sub-task dispatch via ANP wire envelope
- Worker execution with signed response and multimodal PerceptionArtifact extraction
- Zero-tampering verification and result integration
- Integration with Hermes CLI federation commands

Strictly stdlib-only; PEP-420 namespace compliant (no __init__.py).
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from hermes.platform.capabilities.modality.workers import PerceptionArtifact
from hermes.platform.federation.orchestrator import (
    FederatedOrchestrator,
    HandshakeSession,
    HandshakeState,
    ProtocolSecurityError,
    RemoteTaskResult,
)
from hermes.platform.protocols.unified_bus import (
    FederatedAgentDirectory,
    FederatedAgentEndpoint,
    ProtocolEnvelope,
    ProtocolType,
    TrustBoundary,
)

logger = logging.getLogger("hermes.platform.federation.mesh")


class MeshMessageHandler(BaseHTTPRequestHandler):
    """HTTP handler for receiving and dispatching federated envelopes."""

    server: "MeshServer"

    def do_POST(self) -> None:  # noqa: N802
        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len) if content_len > 0 else b"{}"

        try:
            req_json = json.loads(post_data.decode("utf-8"))
            action = req_json.get("action")
            envelope_data = req_json.get("envelope")

            if not envelope_data:
                self._respond_json(400, {"error": "Missing envelope in request"})
                return

            incoming_env = ProtocolEnvelope.from_dict(envelope_data)
            resp_env = self.server.node.handle_incoming_envelope(action, incoming_env)

            if resp_env is not None:
                self._respond_json(200, {
                    "status": "ok",
                    "envelope": resp_env.to_dict(),
                })
            else:
                self._respond_json(200, {"status": "ok", "envelope": None})

        except ProtocolSecurityError as pse:
            self._respond_json(403, {"error": str(pse), "security_error": True})
        except Exception as exc:
            logger.exception("Error processing mesh message")
            self._respond_json(500, {"error": str(exc)})

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._respond_json(200, {
                "status": "healthy",
                "node_id": self.server.node.node_id,
                "host": self.server.node.host,
                "port": self.server.node.port,
            })
        elif self.path == "/directory":
            endpoints = [
                {
                    "agent_id": ep.agent_id,
                    "name": ep.name,
                    "capabilities": ep.capabilities,
                    "node_id": self.server.node.node_id,
                    "host": self.server.node.host,
                    "port": self.server.node.port,
                }
                for ep in self.server.node.directory.list_all()
            ]
            self._respond_json(200, {
                "node_id": self.server.node.node_id,
                "endpoints": endpoints,
            })
        else:
            self._respond_json(404, {"error": "Not found"})

    def _respond_json(self, status_code: int, data: Dict[str, Any]) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


class MeshServer(HTTPServer):
    """HTTP Server hosting a live FederatedMeshNode."""

    def __init__(self, server_address: Tuple[str, int], node: "FederatedMeshNode"):
        self.node = node
        super().__init__(server_address, MeshMessageHandler)


class FederatedMeshNode:
    """A live federated mesh node operating with real network endpoints.

    Integrates:
    - Dedicated HTTP/Socket server listening on (host, port)
    - Underlying FederatedOrchestrator for cryptographic HMAC-SHA256 handshake and ANP routing
    - FederatedAgentDirectory for local and discovered peer agents
    - Client dispatcher connecting to remote peer sockets for wire protocol transmission
    """

    def __init__(
        self,
        node_id: str,
        host: str = "127.0.0.1",
        port: int = 9120,
        orchestrator: Optional[FederatedOrchestrator] = None,
    ) -> None:
        self.node_id = node_id
        self.host = host
        self.port = port
        self.orchestrator = orchestrator or FederatedOrchestrator(node_id=node_id)
        self.directory: FederatedAgentDirectory = self.orchestrator.directory

        self._server: Optional[MeshServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._is_running = False

        # Peer node address registry: peer_node_id -> (host, port)
        self._peer_addresses: Dict[str, Tuple[str, int]] = {}

    @property
    def is_running(self) -> bool:
        return self._is_running

    def register_peer_address(self, peer_node_id: str, host: str, port: int) -> None:
        """Register host and port for remote peer node."""
        self._peer_addresses[peer_node_id] = (host, port)

    def register_peer_secret(self, peer_node_id: str, secret: str) -> None:
        """Register shared secret for HMAC-SHA256 operations with peer."""
        self.orchestrator.register_peer_secret(peer_node_id, secret)

    def register_agent(
        self,
        agent_id: str,
        name: str,
        capabilities: List[str],
        protocol: ProtocolType = ProtocolType.ANP,
    ) -> FederatedAgentEndpoint:
        """Register an agent hosted locally on this node."""
        endpoint = FederatedAgentEndpoint(
            agent_id=agent_id,
            name=name,
            protocol=protocol,
            trust_boundary=TrustBoundary.FEDERATED,
            endpoint_url=f"http://{self.host}:{self.port}/task",
            capabilities=capabilities,
            metadata={"node_id": self.node_id},
        )
        self.directory.register(endpoint)
        return endpoint

    # ========================================================================
    # Server Lifecycle
    # ========================================================================

    def start(self, bind_host: Optional[str] = None) -> None:
        """Start listening on the configured socket host and port."""
        if self._is_running:
            return

        listen_host = bind_host if bind_host is not None else self.host
        # If simulated IP is outside local range (like 100.77.31.78), bind to 127.0.0.1 or 0.0.0.0 if cannot bind
        try:
            self._server = MeshServer((listen_host, self.port), self)
        except OSError:
            # Fallback to localhost if specific mesh IP is non-local virtual IP
            self._server = MeshServer(("127.0.0.1", self.port), self)

        # Update actual bound port if 0 was given
        self.port = self._server.server_address[1]

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name=f"mesh-server-{self.node_id}",
        )
        self._server_thread.start()
        self._is_running = True
        logger.info("FederatedMeshNode '%s' started on %s:%d", self.node_id, self.host, self.port)

    def stop(self) -> None:
        """Shutdown server and cleanup socket."""
        if not self._is_running or not self._server:
            return

        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._is_running = False
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=2.0)
        self._server_thread = None
        logger.info("FederatedMeshNode '%s' stopped", self.node_id)

    # ========================================================================
    # Inbound Message Dispatcher (Invoked by MeshMessageHandler)
    # ========================================================================

    def handle_incoming_envelope(
        self, action: Optional[str], envelope: ProtocolEnvelope
    ) -> Optional[ProtocolEnvelope]:
        """Dispatch incoming envelope according to mesh action type."""
        if action == "handshake_init":
            return self.orchestrator.handle_handshake_request(envelope)
        elif action == "handshake_response":
            return self.orchestrator.complete_handshake(envelope)
        elif action == "handshake_ack":
            self.orchestrator.finalize_responder_handshake(envelope)
            return None
        elif action in ("task_dispatch", "task"):
            return self.orchestrator.execute_incoming_task(envelope)
        else:
            raise ValueError(f"Unknown mesh action: {action}")

    # ========================================================================
    # Outbound Client Transmission Over Network
    # ========================================================================

    def _send_post(
        self,
        target_host: str,
        target_port: int,
        action: str,
        envelope: ProtocolEnvelope,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Execute HTTP POST request carrying an envelope to target peer."""
        conn = http.client.HTTPConnection(target_host, target_port, timeout=timeout)
        try:
            body_dict = {
                "action": action,
                "envelope": envelope.to_dict(),
            }
            body_bytes = json.dumps(body_dict).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Content-Length": str(len(body_bytes)),
            }
            conn.request("POST", "/message", body=body_bytes, headers=headers)
            response = conn.getresponse()
            raw_resp = response.read()

            if response.status >= 400:
                try:
                    err_info = json.loads(raw_resp.decode("utf-8"))
                    msg = err_info.get("error", f"HTTP {response.status}")
                except Exception:
                    msg = f"HTTP {response.status}: {raw_resp.decode('utf-8', errors='ignore')}"
                if response.status == 403:
                    raise ProtocolSecurityError(msg)
                raise RuntimeError(msg)

            return json.loads(raw_resp.decode("utf-8"))
        finally:
            conn.close()

    def discover_peer_endpoints(self, peer_node_id: str) -> List[FederatedAgentEndpoint]:
        """Query peer's /directory endpoint and import discovered agents into local directory."""
        if peer_node_id not in self._peer_addresses:
            raise ValueError(f"No address registered for peer node '{peer_node_id}'")

        host, port = self._peer_addresses[peer_node_id]
        conn = http.client.HTTPConnection(host, port, timeout=5.0)
        try:
            conn.request("GET", "/directory")
            response = conn.getresponse()
            if response.status != 200:
                raise RuntimeError(f"Peer directory discovery failed: HTTP {response.status}")

            data = json.loads(response.read().decode("utf-8"))
            discovered: List[FederatedAgentEndpoint] = []
            secret = self.orchestrator._node_secrets.get(peer_node_id)
            for item in data.get("endpoints", []):
                ep = FederatedAgentEndpoint(
                    agent_id=item["agent_id"],
                    name=item["name"],
                    protocol=ProtocolType.ANP,
                    trust_boundary=TrustBoundary.FEDERATED,
                    endpoint_url=f"http://{host}:{port}/task",
                    capabilities=item.get("capabilities", []),
                    secret_key=secret,
                    metadata={"node_id": peer_node_id},
                )
                self.directory.register(ep)
                # Register in peer_nodes on orchestrator
                if peer_node_id not in self.orchestrator._peer_nodes:
                    self.orchestrator._peer_nodes[peer_node_id] = {
                        "node_id": peer_node_id,
                        "first_seen": time.time(),
                        "agents": [],
                    }
                if item["agent_id"] not in self.orchestrator._peer_nodes[peer_node_id]["agents"]:
                    self.orchestrator._peer_nodes[peer_node_id]["agents"].append(item["agent_id"])
                discovered.append(ep)
            return discovered
        finally:
            conn.close()

    # ========================================================================
    # Mutual 3-Way Live Handshake Over Network
    # ========================================================================

    def perform_live_handshake(
        self,
        peer_node_id: str,
        session_id: Optional[str] = None,
        timeout: float = 5.0,
    ) -> Tuple[HandshakeSession, HandshakeSession]:
        """Perform full live mutual 3-way HMAC-SHA256 handshake over real network connection.

        1. Node A creates initiation envelope with local challenge nonce.
        2. Node A sends envelope over network (POST /message action=handshake_init) to Node B.
        3. Node B receives, verifies signature, produces challenge + responder nonce proof.
        4. Node A receives responder envelope, verifies signature + nonce proof, creates ACK envelope.
        5. Node A sends ACK envelope to Node B (POST /message action=handshake_ack).
        6. Node B finalizes and transitions to ESTABLISHED.
        """
        if peer_node_id not in self._peer_addresses:
            raise ValueError(f"No address registered for peer node '{peer_node_id}'")

        peer_host, peer_port = self._peer_addresses[peer_node_id]

        # Step 1: Initiator step 1
        init_env = self.orchestrator.initiate_handshake(peer_node_id=peer_node_id, session_id=session_id)
        effective_session_id = init_env.metadata["session_id"]

        # Step 2 & 3: Send initiation over the wire to peer
        resp_data = self._send_post(
            peer_host,
            peer_port,
            action="handshake_init",
            envelope=init_env,
            timeout=timeout,
        )
        resp_env_dict = resp_data.get("envelope")
        if not resp_env_dict:
            raise ProtocolSecurityError("Peer returned empty envelope during handshake initiation")
        resp_env = ProtocolEnvelope.from_dict(resp_env_dict)

        # Step 4: Initiator processes responder envelope and produces ACK envelope
        ack_env = self.orchestrator.complete_handshake(resp_env)

        # Step 5: Send ACK envelope over the wire to peer
        self._send_post(
            peer_host,
            peer_port,
            action="handshake_ack",
            envelope=ack_env,
            timeout=timeout,
        )

        init_session = self.orchestrator._handshake_sessions.get(effective_session_id)
        if not init_session or init_session.state != HandshakeState.ESTABLISHED:
            raise ProtocolSecurityError("Handshake failed to reach ESTABLISHED state on initiator")

        from hermes.platform.federation.orchestrator import HandshakeRole

        # Create representation of established state on responder
        responder_session = HandshakeSession(
            session_id=effective_session_id,
            local_node_id=peer_node_id,
            peer_node_id=self.node_id,
            role=HandshakeRole.RESPONDER,
            state=HandshakeState.ESTABLISHED,
            local_nonce=init_session.peer_nonce or "",
            peer_nonce=init_session.local_nonce,
            created_at=init_session.created_at,
            completed_at=time.time(),
        )

        return init_session, responder_session

    # ========================================================================
    # Remote Task Dispatch Over Wire (ANP)
    # ========================================================================

    async def dispatch_task_live(
        self,
        task_id: str,
        target_agent_id: str,
        task_type: str,
        parameters: Dict[str, Any],
        timeout: float = 5.0,
    ) -> RemoteTaskResult:
        """Dispatch remote sub-task to target agent on peer node over the wire.

        1. Validates mutual handshake status with destination node.
        2. Wraps payload into canonical ANP wire envelope.
        3. Signs envelope with node's shared secret.
        4. Transmits over HTTP POST to destination node.
        5. Target node executes task, generates signed response with PerceptionArtifact.
        6. Verifies response signature, asserts zero-tampering, and integrates result.
        """
        endpoint = self.directory.get(target_agent_id)
        if not endpoint:
            raise ValueError(f"Agent '{target_agent_id}' not found in directory")

        peer_node = endpoint.metadata.get("node_id") or getattr(endpoint, "node_id", None)
        if not peer_node:
            raise ValueError(f"Agent '{target_agent_id}' endpoint is missing node_id metadata")

        if not self.orchestrator.is_handshake_established(peer_node):
            raise ProtocolSecurityError(
                f"Cannot dispatch task to '{target_agent_id}': mutual handshake with node '{peer_node}' not established"
            )

        if peer_node not in self._peer_addresses:
            raise ValueError(f"No address registered for peer node '{peer_node}'")

        peer_host, peer_port = self._peer_addresses[peer_node]

        # Prepare signed ANP envelope via orchestrator
        req_env = self.orchestrator.build_task_envelope(
            task_id=task_id,
            target_agent_id=target_agent_id,
            task_type=task_type,
            parameters=parameters,
            require_mutual_handshake=True,
        )

        # Wire transmission
        remote_resp = self._send_post(
            peer_host,
            peer_port,
            action="task_dispatch",
            envelope=req_env,
            timeout=timeout,
        )

        resp_env_dict = remote_resp.get("envelope")
        if not resp_env_dict:
            raise ProtocolSecurityError("Remote peer returned empty envelope for task execution")

        resp_env = ProtocolEnvelope.from_dict(resp_env_dict)

        # Verify signature, zero-tampering, and extract PerceptionArtifact
        return self.orchestrator.verify_and_process_task_response(resp_env, expected_task_id=task_id)


def create_mesh_pair(
    node_a_id: str = "node_local_primary",
    node_b_id: str = "node_worker_auxiliary",
    node_a_addr: Tuple[str, int] = ("127.0.0.1", 9120),
    node_b_addr: Tuple[str, int] = ("127.0.0.1", 9121),
    shared_secret: str = "mesh-shared-secret-key-32bytes!",
) -> Tuple[FederatedMeshNode, FederatedMeshNode]:
    """Helper creating two linked live mesh nodes."""
    node_a = FederatedMeshNode(node_id=node_a_id, host=node_a_addr[0], port=node_a_addr[1])
    node_b = FederatedMeshNode(node_id=node_b_id, host=node_b_addr[0], port=node_b_addr[1])

    node_a.register_peer_secret(node_b_id, shared_secret)
    node_b.register_peer_secret(node_a_id, shared_secret)

    # Register each other's addresses
    node_a.register_peer_address(node_b_id, node_b_addr[0], node_b_addr[1])
    node_b.register_peer_address(node_a_id, node_a_addr[0], node_a_addr[1])

    return node_a, node_b
