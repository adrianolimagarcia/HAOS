from pathlib import Path
from unittest.mock import patch

from hermes.platform.execution.dispatcher import HAOSDispatcher


def test_dispatcher_resolves_explicit_agent_profile_per_task(tmp_path, monkeypatch):
    profile = tmp_path / "profiles" / "research"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    dispatcher = HAOSDispatcher(object())
    with patch("hermes.platform.execution.lane_executor.HermesCliLaneWorker") as factory:
        factory.return_value.available.return_value = True
        worker = dispatcher._worker_for_spec({"agent_profile": "research"}, "hermes")
    factory.assert_called_once_with(profile="research")
    assert worker is factory.return_value


def test_dispatcher_rejects_missing_agent_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    dispatcher = HAOSDispatcher(object())
    with patch("hermes.platform.execution.lane_executor.HermesCliLaneWorker"):
        import pytest
        from hermes.platform.execution.lane_executor import LaneError
        with pytest.raises(LaneError, match="does not exist"):
            dispatcher._worker_for_spec({"agent_profile": "missing"}, "hermes")


def test_dispatcher_keeps_injected_worker_for_unassigned_task():
    injected = object()
    dispatcher = HAOSDispatcher(object(), lane_worker=injected)
    assert dispatcher._worker_for_spec({}, "hermes") is injected
