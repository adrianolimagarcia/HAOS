"""Host-side verifier for the live HAOS federated mesh node running in Docker.

Performs, against http://127.0.0.1:9121 (the containerized Node B), the exact
same steps the `hermes haos federation ping` CLI takes plus a real remote task
dispatch over the ANP wire envelope:

  1. GET  /health      -> node identity + health of the container node
  2. GET  /directory   -> agents the container node publishes
  3. POST /message (handshake_init)    -> step 1 of the mutual 3-way handshake
  4. POST /message (handshake_response)-> step 2 (responder challenge, verified)
  5. POST /message (handshake_ack)     -> step 3 (initiator nonce-proof, verified)
  6. POST /message (task_dispatch)     -> signed remote task to agent_b_docker
  7. verify HMAC-SHA256 signature + extract PerceptionArtifact from the response

The shared secret defaults to `haos-test-secret`, which is exactly the fallback
secret baked into hermes_cli/haos_cmd.py (cmd_haos_federation_ping), and which
the container is launched with via MESH_SECRET.

Stdlib-only + hermes.platform.federation.mesh — no pip dependencies.
Run with the repo venv python from the repo root, e.g.:
    /usr/local/lib/hermes-agent/venv/bin/python deploy/haos-mesh-node/host_verify.py \
        --peer-id node_worker_docker --endpoint 127.0.0.1:9121 --secret haos-test-secret
"""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import sys

# Host-side bootstrap: this script lives under <repo-root>/deploy/haos-mesh-node/,
# but the mesh package lives at <repo-root>/hermes/. Put the repo root on
# sys.path so the script runs without PYTHONPATH gymnastics (still stdlib-only).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(_REPO_ROOT, "hermes")) and _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hermes.platform.capabilities.modality.workers import PerceptionArtifact
from hermes.platform.federation.mesh import FederatedMeshNode
from hermes.platform.federation.orchestrator import HandshakeState


def _http_get(host: str, port: int, path: str) -> dict:
    conn = http.client.HTTPConnection(host, port, timeout=5.0)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        if resp.status >= 400:
            raise RuntimeError(f"GET {path} -> HTTP {resp.status}: {body}")
        return body
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--peer-id", default="node_worker_docker",
                    help="Node B (container) node id (default: node_worker_docker)")
    ap.add_argument("--local-node-id", default="node_local_primary",
                    help="Local (host) node id (default: node_local_primary)")
    ap.add_argument("--endpoint", default="127.0.0.1:9121",
                    help="Container node endpoint host:port (default: 127.0.0.1:9121)")
    ap.add_argument("--secret", default="haos-test-secret",
                    help="Shared HMAC secret (default matches hermes_cli/haos_cmd.py: haos-test-secret)")
    ap.add_argument("--agent-id", default="agent_b_docker",
                    help="Remote agent on Node B to dispatch to (default: agent_b_docker)")
    ap.add_argument("--task-id", default="host-docker-task-0001")
    args = ap.parse_args(argv)

    host, port_str = args.endpoint.split(":")
    port = int(port_str)

    print(f"[verify] Node B (container) endpoint: http://{host}:{port}")
    print(f"[verify] shared secret: {args.secret!r} | local node: {args.local_node_id} | peer node: {args.peer_id}")

    # 1. Health + 2. directory — plain HTTP against the container node.
    health = _http_get(host, port, "/health")
    print(f"[verify] GET /health      -> {json.dumps(health)}")
    directory = _http_get(host, port, "/directory")
    print(f"[verify] GET /directory   -> node_id={directory.get('node_id')} "
          f"agents={[ep['agent_id'] for ep in directory.get('endpoints', [])]}")

    node = FederatedMeshNode(node_id=args.local_node_id, host="127.0.0.1", port=0)
    node.start()
    try:
        node.register_peer_secret(args.peer_id, args.secret)
        node.register_peer_address(args.peer_id, host, port)

        # Discovery (imports Node B agents into the local directory with secret).
        discovered = node.discover_peer_endpoints(args.peer_id)
        print(f"[verify] discovery       -> discovered {len(discovered)} peer endpoint(s): "
              f"{[ep.agent_id for ep in discovered]}")

        # 3-way mutual HMAC-SHA256 live handshake over the wire.
        init_session, resp_session = node.perform_live_handshake(peer_node_id=args.peer_id)
        if init_session.state != HandshakeState.ESTABLISHED:
            raise RuntimeError(f"Handshake did not reach ESTABLISHED: {init_session.state}")
        print(f"[verify] 3-way handshake -> ESTABLISHED session_id={init_session.session_id} "
              f"initiator={init_session.local_node_id} peer={init_session.peer_node_id}")
        print(f"[verify] responder mirror-> ESTABLISHED session_id={resp_session.session_id} "
              f"state={resp_session.state.value}")

        # Remote task dispatch over the ANP wire envelope (action=task_dispatch).
        async def _dispatch() -> object:
            return await node.dispatch_task_live(
                task_id=args.task_id,
                target_agent_id=args.agent_id,
                task_type="multimodal_analysis",
                parameters={
                    "modality": "vision",
                    "target": "docker-mesh-architecture.png",
                    "input_text": "HAOS Mesh Wire Protocol ANP 1.1 (host -> docker node)",
                },
            )

        result = asyncio.run(_dispatch())

        assert result.verified_signature, "response signature was NOT verified"
        assert result.success, "remote task reported failure"
        artifact = result.perception_artifact
        print(f"[verify] task dispatch    -> task_id={result.task_id} "
              f"executed_by={result.remote_agent_id} success={result.success} "
              f"verified_signature={result.verified_signature}")
        print(f"[verify] perception       -> artifact_id={artifact.artifact_id} "
              f"modality={artifact.modality} summary={artifact.summary!r}")
        print(f"[verify] perception       -> extracted_text={artifact.extracted_text!r} "
              f"uncertainty={artifact.uncertainty} produced_by={artifact.produced_by}")
        print("\n✓ LIVE HOST -> CONTAINER MESH VERIFICATION PASSED "
              "(health + directory + 3-way HMAC handshake + signed task dispatch)")
        return 0
    finally:
        node.stop()


if __name__ == "__main__":
    sys.exit(main())
