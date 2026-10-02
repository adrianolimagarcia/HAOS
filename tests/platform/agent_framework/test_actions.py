"""Real file I/O contracts for bounded artifacts, approvals, rollback and recovery."""
import json

import pytest

from hermes.platform.agent_framework.actions import WorkspaceConfigUpdate
from hermes.platform.agent_framework.approvals import ApprovalStore
from hermes.platform.agent_framework.models import ExecutionPlan, PlanStep, StepStatus, RiskTier
from hermes.platform.agent_framework.pipeline import ExecutorAgent, HAOSOrchestrator
from hermes.platform.agent_framework.policy import AgentPolicyEngine


def setup(tmp_path, *, autonomous=False, content=None):
    base = tmp_path / "agent"
    action = WorkspaceConfigUpdate(base)
    step = PlanStep("report", 1, action.name, str(action.target), {"content": content or {"recommendation": "Inspect storage workload"}})
    plan = ExecutionPlan("operational_plan", steps=[step])
    policy = AgentPolicyEngine(base / "policy", auto_load=False)
    policy.update_policy({"action_grants": [{"action": action.name, "target": step.target,
        "params": step.params, "autonomous": autonomous, "allow_rollback": True}]})
    executor = ExecutorAgent(policy, base_dir=base)
    return base, action, step, plan, policy, executor


def test_autonomous_artifact_and_replay_blocked(tmp_path):
    base, action, step, plan, policy, executor = setup(tmp_path, autonomous=True)
    assert not executor.execute(plan)[0].success
    assert not action.target.exists()
    result = executor.execute(plan, dry_run=False, autonomous=True)
    assert result[0].success and action.verify(step)
    receipt = json.loads((base / "receipts/operational_plan.json").read_text())
    assert receipt["phase"] == "finished" and receipt["steps"][0]["postcondition"]
    with pytest.raises(RuntimeError, match="already attempted"):
        executor.execute(plan, dry_run=False, autonomous=True)


def test_approval_binds_intent_expiry_and_persistent_authority(tmp_path):
    base, action, step, plan, policy, executor = setup(tmp_path)
    store = ApprovalStore(base)
    approval = store.issue(plan, step)
    assert store.matches([approval.approval_id], plan, step)
    step.params = {"content": {"tampered": True}}
    assert not store.matches([approval.approval_id], plan, step)
    step.params = policy.to_dict()["action_grants"][0]["params"]
    path = base / "approvals" / f"{approval.approval_id}.json"
    expired = approval.to_dict(); expired["expires_at"] = 0
    path.write_text(json.dumps(expired))
    assert not store.matches([approval.approval_id], plan, step)
    fresh = store.issue(plan, step)
    assert executor.execute(plan, dry_run=False, approvals=[fresh.approval_id])[0].success


def test_failed_postcondition_restores_prestate_and_stops_plan(tmp_path, monkeypatch):
    base, action, step, plan, policy, executor = setup(tmp_path, autonomous=True)
    action.target.parent.mkdir(parents=True)
    original = '{"previous": "state"}\n'
    action.target.write_text(original)
    second = PlanStep("dependent", 2, action.name, step.target, step.params)
    plan.steps.append(second)
    monkeypatch.setattr(executor.actions[action.name], "verify", lambda s: False)
    results = executor.execute(plan, dry_run=False, autonomous=True)
    assert len(results) == 1 and not results[0].success
    assert step.status == StepStatus.ROLLED_BACK and second.status == StepStatus.PENDING
    assert action.target.read_text() == original
    assert executor.last_receipt["phase"] == "rolled_back"


def test_policy_cannot_bypass_target_or_approval_and_crash_blocks_new_plan(tmp_path):
    base, action, step, plan, policy, executor = setup(tmp_path)
    policy.set_action_tier(action.name, RiskTier.SAFE)
    policy.add_to_whitelist(action.name)
    assert not executor.execute(plan, dry_run=False)[0].success
    assert not action.target.exists()
    plan.plan_id = "interrupted"
    executor.receipts.begin(plan)
    plan.plan_id = "new_plan"
    with pytest.raises(RuntimeError, match="recovery"):
        executor.execute(plan, dry_run=False)
    step.target = str(tmp_path / "outside.json")
    with pytest.raises(ValueError, match="dedicated"):
        action.capture(step)


def test_rollback_denial_is_partial_and_blocks_later_mutation(tmp_path, monkeypatch):
    base, action, step, plan, policy, executor = setup(tmp_path, autonomous=True)
    handler = executor.actions[action.name]
    def failed_verify(step):
        policy.update_policy({"action_grants": []})
        return False
    monkeypatch.setattr(handler, "verify", failed_verify)
    assert not executor.execute(plan, dry_run=False, autonomous=True)[0].success
    assert executor.last_receipt["phase"] == "partial"
    assert action.target.exists()
    plan.plan_id = "another"
    with pytest.raises(RuntimeError, match="recovery"):
        executor.execute(plan, dry_run=False, autonomous=True)


def test_dry_cycle_never_invokes_verifier_and_modes_validate(tmp_path):
    class Observer:
        def observe(self): return {"metrics": {}}
    class Diagnostician:
        def diagnose(self, telemetry): return []
    class Verifier:
        def verify(self, *args): raise AssertionError("dry-run verifier invoked")
    orchestrator = HAOSOrchestrator(tmp_path / "agent", observer=Observer(),
                                   diagnostician=Diagnostician(), verifier=Verifier())
    assert orchestrator.run_cycle(mode="dry_run")["outcome"] == "dry_run"
    with pytest.raises(ValueError, match="mode"):
        orchestrator.run_cycle(mode="shell")
