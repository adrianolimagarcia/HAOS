# HAOS second federated mesh node (Node B) running inside Docker.
#
# The hermes/platform tree is strictly stdlib-only and PEP-420 (namespace
# package, no __init__.py), so the whole tree copies in with zero pip deps.
# mesh.py pulls in, at runtime:
#   hermes.platform.capabilities.modality.workers
#   hermes.platform.federation.orchestrator
#   hermes.platform.protocols.unified_bus
# all of which are stdlib-only at module scope.
#
# Build from the REPO ROOT so `hermes/platform/` is inside the build context:
#   docker build -f deploy/haos-mesh-node/Dockerfile -t haos-mesh-node .
# (the repo-root .dockerignore prunes .git/node_modules/__pycache__/...)

FROM python:3.11-slim

WORKDIR /app

# Copy the whole stdlib-only hermes/platform tree (PEP-420 namespace package).
COPY hermes/platform /app/hermes/platform
# Second-node entrypoint (env-configured, registers demo agent + peer secret).
COPY deploy/haos-mesh-node/node_b_server.py /app/node_b_server.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    MESH_HOST=0.0.0.0 \
    MESH_PORT=9121

EXPOSE 9121

# Run as an unprivileged user (numeric id works even where useradd is absent).
# chmod: this workspace's hermes/platform tree and the entrypoint can be 0600
# (tool-created copies); the non-root runtime user must be able to read all of
# it, so normalize to a+rX before dropping privileges.
RUN chmod 0644 /app/node_b_server.py && chmod -R a+rX /app/hermes && \
    (command -v useradd >/dev/null && useradd --create-home --uid 10001 meshuser) || true
USER 10001

# /health is served by the mesh node itself (MeshMessageHandler.do_GET).
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:9121/health')); assert d.get('status')=='healthy', d"

ENTRYPOINT ["python", "/app/node_b_server.py"]
