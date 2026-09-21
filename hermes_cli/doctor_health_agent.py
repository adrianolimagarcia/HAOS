"""Optional Rust health-agent check for ``hermes doctor``."""
from __future__ import annotations

from hermes_cli.doctor_report import Finding, check_fail, check_ok, check_warn, doctor_check


@doctor_check(on_error="Health agent check failed: {e}")
def _check_health_agent(should_fix: bool, f: Finding) -> None:
    from tools.environments.local_health_agent import _config, required_healthcheck

    cfg = _config()
    if not cfg.get("enabled", False):
        return
    ok, detail = required_healthcheck()
    required = bool(cfg.get("required", False))
    if ok:
        check_ok("Rust health agent", detail)
    elif required:
        check_fail("Rust health agent", detail)
        f.issues.append(f"Required Rust health agent failed: {detail}")
    else:
        check_warn("Rust health agent (optional)", detail)


check_health_agent = _check_health_agent
