#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMAGE="${RUST_NATIVE_IMAGE:-hermes-rust-native-validation:local}"
RECEIPT="${RUST_NATIVE_RECEIPT:-$(mktemp)}"
cleanup(){ rm -f "$RECEIPT"; }
trap cleanup EXIT

docker build --file "$ROOT/SDD/rust-native/harness/Dockerfile" --tag "$IMAGE" "$ROOT" >/dev/null
docker run --rm --init --cgroupns=host "$IMAGE" bash -s >"$RECEIPT" <<'INNER'
set -euo pipefail
python3 - <<'PY'
import json, pathlib, subprocess
checks=[]
for manifest in sorted(pathlib.Path("packages").glob("*/Cargo.toml")):
    p=subprocess.run(["cargo","test","--manifest-path",str(manifest)],capture_output=True,text=True)
    checks.append({"manifest":str(manifest),"status":"passed" if p.returncode==0 else "failed"})
    if p.returncode: print(p.stderr, file=__import__("sys").stderr); raise SystemExit(p.returncode)
print(json.dumps({"status":"observed","checks":checks,"cgroup_v2":pathlib.Path("/sys/fs/cgroup/cgroup.controllers").is_file()},sort_keys=True))
PY
INNER
cat "$RECEIPT"
