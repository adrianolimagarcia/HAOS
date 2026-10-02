"""Cancellation and operator authorization exercise real persistent artifact paths."""
import asyncio

import pytest

from hermes.platform.agent_framework.pipeline import HAOSOrchestrator, PlannerAgent
from hermes.platform.agent_framework.policy import AgentPolicyEngine


def test_cross_profile_policy_symlinks_never_read_or_overwrite(tmp_path):
    profile_a, profile_b = tmp_path / "a/agent", tmp_path / "b/agent"
    profile_a.mkdir(parents=True); profile_b.mkdir(parents=True)
    external = profile_b / "agent-policy.yaml"
    external.write_text('{}\n')
    original = external.read_bytes()
    linked = profile_a / "agent-policy.yaml"
    linked.symlink_to(external)
    with pytest.raises(ValueError, match="Symlink"):
        AgentPolicyEngine(linked)
    policy = AgentPolicyEngine(profile_a / "regular.yaml", auto_load=False)
    with pytest.raises(ValueError, match="Symlink"):
        policy.load_from_yaml(linked)
    with pytest.raises(ValueError, match="Symlink"):
        policy.save_to_yaml(linked)
    linked.unlink()
    orchestrator = HAOSOrchestrator(profile_a, observer=Observer(), diagnostician=Diagnostician(),
        planner=PlannerAgent(report_target=profile_a / "artifacts/operational-report.json"))
    plan_id = orchestrator.run_cycle()["plan"]["plan_id"]
    linked.symlink_to(external)
    with pytest.raises(ValueError, match="Symlink"):
        orchestrator.grant(plan_id, "report", autonomous=True)
    assert external.read_bytes() == original
    linked.unlink()
    regular = AgentPolicyEngine(linked, auto_load=False)
    regular.save_to_yaml()
    assert AgentPolicyEngine(linked).to_dict()["action_grants"] == []


class Observer:
    def observe(self):
        return {"metrics": {}}


class Diagnostician:
    def diagnose(self, telemetry):
        return []


def test_operator_grant_preview_apply_and_cancel_restore(tmp_path, monkeypatch):
    base = tmp_path / "agent"
    orchestrator = HAOSOrchestrator(base, observer=Observer(), diagnostician=Diagnostician(),
        planner=PlannerAgent(report_target=base / "artifacts/operational-report.json"))
    preview = orchestrator.run_cycle()
    plan_id = preview["plan"]["plan_id"]
    grant = orchestrator.grant(plan_id, "report", autonomous=True)
    assert grant["allow_rollback"] is True
    assert orchestrator.grant(plan_id, "report", autonomous=True) == grant
    assert len(orchestrator.executor.policy.to_dict()["action_grants"]) == 1
    action = orchestrator.executor.actions["workspace_config_update"]
    real_apply = action.apply
    def interrupted(step):
        real_apply(step)
        raise asyncio.CancelledError("operator cancellation")
    monkeypatch.setattr(action, "apply", interrupted)
    with pytest.raises(asyncio.CancelledError):
        orchestrator.run_cycle(mode="autonomous", plan=orchestrator.load_plan(plan_id))
    assert not action.target.exists()
    assert orchestrator.executor.last_receipt["phase"] == "rolled_back"
    assert orchestrator.executor.last_receipt["steps"][0]["before"] == {"exists": False}
