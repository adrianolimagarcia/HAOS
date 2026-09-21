import json, os, stat, subprocess
from pathlib import Path


def test_health_agent_defaults():
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["terminal"]["health_agent"] == {
        "enabled": False, "binary": "", "required": False, "timeout_ms": 2000
    }


def test_health_agent_validates_bounded_schema(tmp_path, monkeypatch):
    script = tmp_path / "health"
    script.write_text("#!/bin/sh\nprintf '%s' '{\"schema\":\"haos.health.v1\",\"liveness\":true,\"readiness\":true}'\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr("tools.environments.local_health_agent._config", lambda: {"enabled": True, "binary": str(script)})
    from tools.environments.local_health_agent import healthcheck
    assert healthcheck()[0]


def test_health_agent_timeout_and_required(tmp_path, monkeypatch):
    script = tmp_path / "health"
    script.write_text("#!/bin/sh\nsleep 2\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr("tools.environments.local_health_agent._config", lambda: {"enabled": True, "binary": str(script), "required": True, "timeout_ms": 50})
    from tools.environments.local_health_agent import required_healthcheck
    ok, reason = required_healthcheck()
    assert not ok and "required" in reason


def test_doctor_health_agent_optional_warning(monkeypatch, capsys):
    from hermes_cli import doctor_health_agent
    monkeypatch.setattr("tools.environments.local_health_agent._config", lambda: {"enabled": True, "required": False})
    monkeypatch.setattr("tools.environments.local_health_agent.required_healthcheck", lambda: (False, "probe failed"))
    finding = doctor_health_agent._check_health_agent(False)
    assert not finding.issues
    assert "optional" in capsys.readouterr().out




def test_health_agent_rejects_large_output(tmp_path, monkeypatch):
    script = tmp_path / "health"
    script.write_text("#!/bin/sh\nhead -c 70000 /dev/zero\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr("tools.environments.local_health_agent._config", lambda: {"enabled": True, "binary": str(script)})
    from tools.environments.local_health_agent import healthcheck
    ok, reason = healthcheck()
    assert not ok and "maximum size" in reason
    from hermes_cli import doctor_health_agent
    monkeypatch.setattr("tools.environments.local_health_agent._config", lambda: {"enabled": True, "required": True})
    monkeypatch.setattr("tools.environments.local_health_agent.required_healthcheck", lambda: (False, "probe failed"))
    finding = doctor_health_agent._check_health_agent(False)
    assert finding.issues and "Required" in finding.issues[0]
