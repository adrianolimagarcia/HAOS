# HAOS second federated mesh node — Docker deployment (Node B)

Runs a **real, second federated node** of the HAOS mesh layer inside Docker and
proves a live 3-way HMAC-SHA256 mutual handshake + signed task dispatch **from
the host node (A) into the container node (B)**.

Everything under `hermes/platform/` is strictly stdlib-only and PEP-420
(namespace package — no `__init__.py`, no pip deps), so the image simply copies
that tree and needs no `pip install`.

- Node A (host): `node_local_primary` — the `hermes haos federation ping` CLI.
- Node B (container): `node_worker_docker` on `0.0.0.0:9121` (published to host
  port 9121), hosting demo agent **`agent_b_docker`**.
- Shared secret: `haos-test-secret` — this is exactly the fallback secret baked
  into `hermes_cli/haos_cmd.py` (`cmd_haos_federation_ping`), so the container
  secret matches what the CLI expects with no extra flags.

## Build

Build from the **repo root** so `hermes/platform/` is inside the build context
(the repo-root `.dockerignore` prunes `.git`/`node_modules`/`__pycache__`/…):

```bash
docker build -f deploy/haos-mesh-node/Dockerfile -t haos-mesh-node .
```

## Run

```bash
docker run -d --name haos-node-b -p 9121:9121 \
  -e NODE_ID=node_worker_docker \
  -e MESH_HOST=0.0.0.0 \
  -e MESH_PORT=9121 \
  -e MESH_SECRET=haos-test-secret \
  -e PEER_NODE_ID=node_local_primary \
  haos-mesh-node
```

Check readiness:

```bash
docker ps --filter name=haos-node-b
docker logs haos-node-b                 # look for: [mesh-node-b] READY ...
curl -s http://127.0.0.1:9121/health    # {"status":"healthy","node_id":"node_worker_docker",...}
curl -s http://127.0.0.1:9121/directory # {"node_id":"node_worker_docker","endpoints":[agent_b_docker...]}
```

## Prove the live host → container mesh (3-way handshake)

Real CLI (host node A → container node B). The peer id is the container's
`NODE_ID`; `--endpoint` is the published host port; the default secret
`haos-test-secret` is used unless `--secret` is given:

```bash
cd <repo-root>
HERMES_HOME=/root/.hermes PYTHONPATH=. \
  /usr/local/lib/hermes-agent/venv/bin/python hermes_cli/main.py \
  haos federation ping node_worker_docker \
  --endpoint 127.0.0.1:9121 --local-node-id node_local_primary --secret haos-test-secret
```

Expected success output:

```
Initiating mutual 3-way HMAC handshake with peer 'node_worker_docker'...
✓ Handshake successful! Mutual HMAC-SHA256 authenticated with 'node_worker_docker'.
  Session ID: hs-…
  State: established
```

Optional: full verifier — health + directory + discovery + 3-way handshake +
**signed remote task dispatch** (multimodal PerceptionArtifact) in one run:

```bash
cd <repo-root>
/usr/local/lib/hermes-agent/venv/bin/python deploy/haos-mesh-node/host_verify.py \
  --peer-id node_worker_docker --endpoint 127.0.0.1:9121 \
  --secret haos-test-secret --agent-id agent_b_docker
```

## Stop / clean up

```bash
docker stop haos-node-b      # SIGTERM -> entrypoint stops the mesh node cleanly
docker rm haos-node-b
```

## Layout

| File | Purpose |
|---|---|
| `Dockerfile` | `python:3.11-slim`, copies `hermes/platform` (stdlib-only) + entrypoint, non-root user, EXPOSE 9121, HEALTHCHECK on `/health` |
| `node_b_server.py` | Env-configured Node B: registers demo agent `agent_b_docker`, peer secret for Node A, starts mesh server, SIGTERM clean stop |
| `host_verify.py` | Host-side stdlib-only verifier: health/directory/handshake/task-dispatch against `127.0.0.1:9121` |
