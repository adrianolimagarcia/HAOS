#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMAGE="${RUST_NATIVE_IMAGE:-hermes-rust-native-validation:local}"
RECEIPT="${RUST_NATIVE_RECEIPT:-$ROOT/SDD/rust-native/harness/receipt.json}"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
mkdir -p "$(dirname "$RECEIPT")"

build_rc=0
docker build --file "$ROOT/SDD/rust-native/harness/Dockerfile" --tag "$IMAGE" "$ROOT" >"$TMPDIR/build.out" 2>"$TMPDIR/build.err" || build_rc=$?
cat "$TMPDIR/build.out"
cat "$TMPDIR/build.err" >&2
if [ "$build_rc" -ne 0 ]; then
  python3 - "$RECEIPT" "$build_rc" "$TMPDIR/build.err" <<'PY'
import json, pathlib, sys
r={"schema":"haos.rust-native.harness.v1","status":"build_failed","failure_class":"build_failed","exit_code":int(sys.argv[2]),"stdout_tail":"","stderr_tail":pathlib.Path(sys.argv[3]).read_text(errors="replace")[-4096:],"rust":[],"python":[],"cgroup":{}}
pathlib.Path(sys.argv[1]).write_text(json.dumps(r,sort_keys=True)+"\n"); print(json.dumps(r,sort_keys=True)); raise SystemExit(1)
PY
fi

run_rc=0
docker run --rm --init -i --cap-add=SYS_ADMIN --cgroupns=host -v "$ROOT/tests:/workspace/tests:ro" "$IMAGE" bash -s >"$TMPDIR/run.out" 2>"$TMPDIR/run.err" <<'INNER'
set -u
python3 - <<'PY'
import json, pathlib, platform, subprocess, sys, time

def command(argv):
    p=subprocess.run(argv,capture_output=True,text=True)
    return {"command":argv,"exit_code":p.returncode,"status":"passed" if p.returncode==0 else "tests_failed","stdout_tail":p.stdout[-4096:],"stderr_tail":p.stderr[-4096:]}
started=time.time(); rust=[]; python=[]
for argv in (["cargo","test","--workspace"], ["python3","-m","unittest","packages/haos-exec/tests/test_protocol.py","-v"], ["python3","-m","pytest","tests/tools/test_local_health_agent.py","-q"]):
    item=command(argv); (rust if argv[0]=="cargo" else python).append(item)
c=pathlib.Path("/sys/fs/cgroup/cgroup.controllers")
cgroup={"v2_visible":c.is_file(),"controllers":c.read_text().split() if c.is_file() else [],"delegated":False}
if c.is_file():
    probe=pathlib.Path("/sys/fs/cgroup/hermes-harness-probe")
    try:
        probe.mkdir(); cgroup["delegated"]=True; probe.rmdir()
    except OSError as e: cgroup["probe_error"]=str(e)
if any(x["status"]=="failed" for x in rust+python): failure="tests_failed"
elif not cgroup["v2_visible"]: failure="cgroup_unavailable"
elif not cgroup["delegated"]: failure="cgroup_not_delegated"
else: failure=None
r={"schema":"haos.rust-native.harness.v1","status":"passed" if failure is None else failure,"failure_class":failure,"kernel":platform.release(),"architecture":platform.machine(),"container_exit_code":0 if failure is None else 1,"rust":rust,"python":python,"cgroup":cgroup,"stdout_tail":"","stderr_tail":"","duration_seconds":round(time.time()-started,3)}
print("RUST_NATIVE_RECEIPT="+json.dumps(r,sort_keys=True),flush=True)
sys.exit(0 if failure is None else 1)
PY
INNER
run_rc=$?
cat "$TMPDIR/run.out"
cat "$TMPDIR/run.err" >&2

python3 - "$TMPDIR/run.out" "$TMPDIR/run.err" "$RECEIPT" "$run_rc" <<'PY'
import json, pathlib, sys
out=pathlib.Path(sys.argv[1]).read_text(errors="replace")
err=pathlib.Path(sys.argv[2]).read_text(errors="replace")[-4096:]
r=None
for line in out.splitlines():
    if line.startswith("RUST_NATIVE_RECEIPT="):
        try: r=json.loads(line.split("=",1)[1])
        except json.JSONDecodeError: pass
if r is None:
    r={"schema":"haos.rust-native.harness.v1","status":"runtime_failed","failure_class":"runtime_failed","container_exit_code":int(sys.argv[4]),"rust":[],"python":[],"cgroup":{},"stdout_tail":out[-4096:],"stderr_tail":err}
else:
    r["container_exit_code"]=int(sys.argv[4]); r["stdout_tail"]=out[-4096:]; r["stderr_tail"]=err
pathlib.Path(sys.argv[3]).write_text(json.dumps(r,sort_keys=True)+"\n")
print(json.dumps(r,sort_keys=True))
sys.exit(0 if r.get("status")=="passed" and int(sys.argv[4])==0 else 1)
PY
