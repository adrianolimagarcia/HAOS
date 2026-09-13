"""HAOS second federated mesh node (Node B) container entrypoint.

Reads its identity + the shared mesh secret from the environment, registers a
local demo agent, registers the peer secret/address for the primary node (A),
and serves the live mesh HTTP endpoints (/health, /directory, /message).

Environment:
    NODE_ID        node identity, e.g. node_worker_docker  (default)
    MESH_HOST      bind host                                  (default 0.0.0.0)
    MESH_PORT      bind port                                  (default 9121)
    MESH_SECRET    shared HMAC secret with peer (Node A)      (default matches
                   the hermes_cli/haos_cmd.py fallback: haos-test-secret)
    PEER_NODE_ID   peer node id to register secret/address for (Node A)
                   (default node_local_primary — the CLI --local-node-id)
    PEER_HOST      peer (Node A) host                          (default 127.0.0.1)
    PEER_PORT      peer (Node A) port; when set, also registers A's address
                   so this node could initiate toward A in the future.

Stdlib-only + hermes.platform.federation.mesh — no pip dependencies.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from hermes.platform.federation.mesh import FederatedMeshNode
from hermes.platform.protocols.unified_bus import ProtocolType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("haos.mesh.node_b")

AGENT_ID = "agent_b_docker"


def main() -> int:
    node_id = os.environ.get("NODE_ID", "node_worker_docker")
    mesh_host = os.environ.get("MESH_HOST", "0.0.0.0")
    mesh_port = int(os.environ.get("MESH_PORT", "9121"))
    secret = os.environ.get("MESH_SECRET", "haos-test-secret")
    peer_node_id = os.environ.get("PEER_NODE_ID", "node_local_primary")
    peer_host = os.environ.get("PEER_HOST", "127.0.0.1")
    peer_port_raw = os.environ.get("PEER_PORT", "")

    node = FederatedMeshNode(node_id=node_id, host=mesh_host, port=mesh_port)

    # Local demo agent hosted on this (containerized) node.
    node.register_agent(
        agent_id=AGENT_ID,
        name="Node B Docker Demo Worker",
        capabilities=["modality:vision", "modality:perception", "reasoning:deep"],
        protocol=ProtocolType.ANP,
    )

    # Shared secret + address for the primary node (Node A).
    node.register_peer_secret(peer_node_id, secret)
    if peer_port_raw:
        node.register_peer_address(peer_node_id, peer_host, int(peer_port_raw))

    # Start the live mesh HTTP server (binds 0.0.0.0:<MESH_PORT> in the container).
    node.start()

    stop_event = threading.Event()

    def _handle_term(signum, frame):  # noqa: ARG001
        logger.info("Received signal %s — stopping mesh node '%s'", signum, node.node_id)
        try:
            node.stop()
        finally:
            stop_event.set()

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    print(
        f"[mesh-node-b] READY node_id={node.node_id} "
        f"listening_on={mesh_host}:{node.port} "
        f"agent={AGENT_ID} peer={peer_node_id} secret_configured={'yes' if secret else 'no'}",
        flush=True,
    )

    # Keep the main thread alive; SIGTERM (docker stop) tears the node down cleanly.
    while not stop_event.wait(1.0):
        if not node.is_running:
            logger.warning("Mesh node '%s' is no longer running; exiting", node.node_id)
            break

    print("[mesh-node-b] clean shutdown complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
