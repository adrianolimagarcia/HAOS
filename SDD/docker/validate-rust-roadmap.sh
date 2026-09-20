#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="hermes-rust-validation:local"
docker build --file "$ROOT/SDD/docker/Dockerfile.rust-validation" --tag "$IMAGE" "$ROOT"
docker run --rm --cap-add=SYS_ADMIN --cgroupns=host "$IMAGE" bash -lc '
  set -euo pipefail
  python3 -m unittest discover -s packages/haos-exec/tests -v
  test -x packages/haos-exec/target/release/hermes-exec
  python3 - <<'PY'
import json, os, subprocess, tempfile
root = tempfile.mkdtemp()
req = {"id": 1, "method": "exec", "params": {"argv": ["printf", "container-ok"], "cwd": "."}}
p = subprocess.run(["packages/haos-exec/target/release/hermes-exec"], input=json.dumps(req)+"\n", text=True, capture_output=True, env={**os.environ, "HERMES_EXEC_ROOT": root}, check=True)
assert "container-ok" in p.stdout, p.stdout
print("container-smoke-ok")
PY
  printf "cgroup.controllers: "; cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null || true
'
