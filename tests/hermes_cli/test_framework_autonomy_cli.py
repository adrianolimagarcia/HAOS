"""Real parser, subprocess profile isolation and foreground worker contracts."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from hermes_cli.main import _build_cli_parser

ROOT = Path(__file__).resolve().parents[2]


def invoke(home, *parts):
    env = os.environ.copy()
    env.pop("HAOS_HOME", None)
    env.update(HERMES_HOME=str(home), HOME=str(home.parent), PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, "-m", "hermes_cli.main", "framework", "autonomy",
                             *parts, "--json"], env=env, cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    return result.returncode, json.loads(result.stdout)


def test_profile_status_pause_resume_and_events_are_persisted(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for home in (a, b, a):
        code, state = invoke(home, "status")
        assert code == 0 and state["base_dir"] == str(home / "agent")
        assert state["queue"]["available"] is False
        assert state["heartbeat"] is None
        # The CLI entrypoint logs, but inspection must not bootstrap framework/config.
        assert not (home / "agent").exists()
        assert not (home / "config.yaml").exists()
    assert invoke(a, "pause") == (0, {"paused": True})
    code, state = invoke(a, "status")
    assert code == 0 and state["queue"]["paused"] is True
    assert invoke(b, "status")[1]["queue"]["available"] is False
    assert invoke(a, "resume") == (0, {"paused": False})
    for bad in ("", "  ", "x" * 2001):
        assert invoke(a, "event", "--summary", bad)[0] == 1
    summary = "Investigate backup failure; never run rm -rf /"
    assert invoke(a, "event", "--summary", summary)[0] == 0
    assert invoke(a, "event", "--summary", summary)[0] == 1
    assert invoke(a, "event", "--summary", "A distinct problem")[0] == 0
    jobs = invoke(a, "status")[1]["queue"]["recent_jobs"]
    assert len(jobs) == 2
    event = next(job["event"] for job in jobs if job["event"]["payload"]["summary"] == summary)
    assert event["source"] == "cli" and event["event_type"] == "operator.investigation"
    assert event["payload"]["issue_id"] == hashlib.sha256(summary.encode()).hexdigest()
    assert event["metadata"] == {}
    assert invoke(b, "status")[1]["queue"]["recent_jobs"] == []


def test_parser_rejects_command_injection_surface_and_disabled_serve(tmp_path):
    parser, _ = _build_cli_parser()
    for leaf in ("status", "pause", "resume", "serve"):
        args = parser.parse_args(["framework", "autonomy", leaf, "--base-dir", str(tmp_path), "--json"])
        assert args.autonomy_action == leaf and args.json and args.base_dir == str(tmp_path)
    for option in ("--command", "--concurrency", "--enabled", "--auto-apply"):
        with pytest.raises(SystemExit):
            parser.parse_args(["framework", "autonomy", "event", "--summary", "inspect", option, "true"])
    code, result = invoke(tmp_path / "disabled", "serve")
    assert code == 1 and "enabled" in result["error"]
    assert not (tmp_path / "disabled" / "agent").exists()


def test_foreground_serve_signal_stops_actual_service(tmp_path, monkeypatch, capsys):
    from hermes.platform.agent_framework.autonomy_service import AutonomyService
    import hermes.platform.agent_framework.autonomy_service as service_module

    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text("framework:\n  autonomy:\n    enabled: true\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    services = []

    class NoFindings:
        def observe(self):
            return {"metrics": {}, "findings": []}

    class NoExternalInvestigation:
        def investigate(self, *_args, **_kwargs):
            raise AssertionError("No investigation is expected for an empty queue")

    def factory(**kwargs):
        service = AutonomyService(**kwargs, observer=NoFindings(),
                                  investigator=NoExternalInvestigation())
        original_tick = service.tick

        def tick():
            result = original_tick()
            signal.raise_signal(signal.SIGTERM)
            return result

        monkeypatch.setattr(service, "tick", tick)
        services.append(service)
        return service

    monkeypatch.setattr(service_module, "AutonomyService", factory)
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    parser, _ = _build_cli_parser()
    args = parser.parse_args(["framework", "autonomy", "serve", "--json"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "stopped"}
    assert services
    assert all(signal.getsignal(sig) == handler for sig, handler in before.items())
    heartbeat = json.loads((home / "agent" / "autonomy" / "service.json").read_text())
    assert heartbeat["status"] == "stopped"
