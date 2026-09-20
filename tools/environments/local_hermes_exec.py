"""Optional hermes-exec sidecar adapter for LocalEnvironment."""
from __future__ import annotations
import json, os, subprocess
from tools.environments.base_output import _ThreadedProcessHandle

def _config():
    try:
        from hermes_cli.config import load_config
        return ((load_config() or {}).get("terminal") or {}).get("hermes_exec") or {}
    except Exception:
        return {}

def configured_binary() -> str:
    cfg = _config()
    return str(cfg.get("binary") or os.environ.get("HERMES_EXEC_BIN") or "").strip()

def enabled() -> bool:
    cfg = _config()
    return bool(cfg.get("enabled", False)) and bool(configured_binary())

def healthcheck() -> tuple[bool, str]:
    binary = configured_binary()
    if not binary: return False, "hermes-exec is not configured"
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK): return False, f"not executable: {binary}"
    return True, binary


    binary = configured_binary()
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        raise RuntimeError(f"configured hermes-exec is not executable: {binary}")
    def run():
        child_env = dict(env)
        child_env["HERMES_EXEC_ROOT"] = cwd
        req = {"id": 1, "method": "exec", "params": {"command": command, "cwd": ".", "timeout_ms": int(timeout) * 1000, "stdin": stdin_data}}
        try:
            p = subprocess.run([binary], input=json.dumps(req)+"\n", text=True, capture_output=True, env=child_env, timeout=max(1, int(timeout)+5))
        except subprocess.TimeoutExpired:
            return "hermes-exec healthcheck timeout", 124
        if p.returncode:
            return p.stderr or p.stdout, p.returncode
        try:
            msg = json.loads(p.stdout.splitlines()[-1])
            if msg.get("error"): return msg["error"].get("message", "hermes-exec error"), 1
            result = msg.get("result", {})
            return result.get("output", ""), int(result.get("returncode", 1))
        except (ValueError, IndexError, TypeError) as exc:
            return f"hermes-exec protocol error: {exc}", 1
    return _ThreadedProcessHandle(run)
