"""Optional profile-scoped health-agent probe."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

MAX_HEALTH_BYTES = 64 * 1024


def _config() -> dict:
    try:
        from hermes_cli.config import load_config
        terminal = (load_config() or {}).get("terminal") or {}
        return terminal.get("health_agent") or {}
    except Exception:
        return {}


def configured_binary() -> str:
    return str(_config().get("binary") or "").strip()


def enabled() -> bool:
    cfg = _config()
    return bool(cfg.get("enabled", False)) and bool(configured_binary())


def _validate_snapshot(raw: str) -> tuple[bool, str]:
    if len(raw.encode("utf-8", "replace")) > MAX_HEALTH_BYTES:
        return False, "health snapshot exceeds maximum size"
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return False, f"invalid health JSON: {exc}"
    if not isinstance(value, dict) or value.get("schema") != "haos.health.v1":
        return False, "invalid health schema"
    if not isinstance(value.get("liveness"), bool) or not isinstance(value.get("readiness"), bool):
        return False, "invalid health status fields"
    if not value["liveness"]:
        return False, "health agent is not live"
    if not value["readiness"]:
        return False, "health agent is not ready"
    return True, "ok"


def healthcheck() -> tuple[bool, str]:
    """Run the configured agent with a bounded timeout in the active profile."""
    cfg = _config()
    if not cfg.get("enabled", False):
        return True, "disabled"
    binary = configured_binary()
    if not binary:
        return False, "health agent is enabled but no binary is configured"
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        return False, f"not executable: {binary}"
    try:
        timeout_ms = int(cfg.get("timeout_ms", 2000))
    except (TypeError, ValueError):
        timeout_ms = 2000
    timeout = max(0.1, min(timeout_ms, 30_000) / 1000.0)
    env = {"PATH": os.environ.get("PATH", "")}
    try:
        from hermes_cli.config import get_hermes_home
        env["HERMES_HOME"] = str(get_hermes_home())
    except Exception:
        pass
    try:
        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
            proc = subprocess.run([binary], stdout=stdout_file, stderr=stderr_file, timeout=timeout, check=False, env=env)
            stdout_file.seek(0)
            raw_bytes = stdout_file.read(MAX_HEALTH_BYTES + 1)
            if len(raw_bytes) > MAX_HEALTH_BYTES:
                return False, "health snapshot exceeds maximum size"
            raw = raw_bytes.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return False, "health agent timeout"
    if proc.returncode != 0:
        return False, f"health agent exited with {proc.returncode}"
    ok, reason = _validate_snapshot(raw)
    return (ok, binary if ok else reason)


def required_healthcheck() -> tuple[bool, str]:
    """Probe and apply fail-closed semantics for ``required: true``."""
    cfg = _config()
    ok, detail = healthcheck()
    if not ok and bool(cfg.get("required", False)):
        return False, f"required health agent failed: {detail}"
    return ok, detail
