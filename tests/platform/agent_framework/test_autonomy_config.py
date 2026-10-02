"""Canonical config reads are profile-bound, bounded and fail closed."""
from pathlib import Path

import pytest
import yaml

from hermes.platform.agent_framework.autonomy_config import AutonomyConfig
from hermes_cli.config_defaults import DEFAULT_CONFIG
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def test_config_profile_alternation_defaults_and_loop_exclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "launch"))
    for name, enabled, poll in (("a", True, 7), ("b", False, 21), ("a", True, 7)):
        home = tmp_path / name
        home.mkdir(exist_ok=True)
        (home / "config.yaml").write_text(yaml.safe_dump({"framework": {"autonomy": {
            "enabled": enabled, "poll_seconds": poll, "event_patterns": ["*"]}}}))
        token = set_hermes_home_override(home)
        try:
            cfg = AutonomyConfig.load()
            assert cfg.enabled is enabled and cfg.poll_seconds == poll
            assert cfg.max_pending == DEFAULT_CONFIG["framework"]["autonomy"]["max_pending"]
            assert cfg.auto_apply is False
            assert cfg.matches("backup.failed")
            assert not cfg.matches("pipeline.failed") and not cfg.matches("autonomy.error")
        finally:
            reset_hermes_home_override(token)
    token = set_hermes_home_override(tmp_path.joinpath("missing"))
    try:
        cfg = AutonomyConfig.load()
        assert cfg == AutonomyConfig()
        assert not (tmp_path / "missing").exists()
    finally:
        reset_hermes_home_override(token)


@pytest.mark.parametrize("values", [
    {"enabled": "true"}, {"auto_apply": 1}, {"poll_seconds": True},
    {"poll_seconds": 0}, {"telemetry_seconds": 14}, {"cooldown_seconds": 29},
    {"max_pending": 129}, {"max_jobs_per_hour": 25}, {"investigation_timeout": 181},
    {"max_output_tokens": 4097}, {"event_patterns": []}, {"event_patterns": "*"},
    {"event_patterns": [123]}, {"event_patterns": ["x" * 129]}, {"concurrency": 2},
])
def test_invalid_config_never_silently_relaxes_limits(tmp_path, monkeypatch, values):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"framework": {"autonomy": values}}))
    with pytest.raises(ValueError):
        AutonomyConfig.load()
