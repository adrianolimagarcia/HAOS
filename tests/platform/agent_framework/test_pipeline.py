"""Behavioral contracts: no host mutations, fabricated failures, or dry-run repair."""
import json

import pytest

from hermes.platform.agent_framework.models import (
    ExecutionPlan, ExecutionResult, PlanStep, PlanStatus, RiskTier, StepStatus,
)
from hermes.platform.agent_framework.pipeline import (
    ObserverAgent, DiagnosticianAgent, ExecutorAgent, HAOSOrchestrator, VerifierAgent,
)
from hermes.platform.agent_framework.policy import AgentPolicyEngine, PolicyDecision


@pytest.fixture
def observer(tmp_path):
    proc, sys = tmp_path / "proc", tmp_path / "sys"
    (proc / "sys/vm").mkdir(parents=True)
    (sys / "devices/system/cpu").mkdir(parents=True)
    (proc / "loadavg").write_text("8.0 7.0 6.0 1/200 100\n")
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 50 kB\nDirty: 5 kB\nWriteback: 2 kB\n")
    (proc / "diskstats").write_text("8 0 sda 1 0 8 0 2 0 16 0 0 42 42\n")
    (proc / "sys/vm/dirty_ratio").write_text("20\n")
    (sys / "devices/system/cpu/online").write_text("0-3\n")
    return ObserverAgent(proc, sys)


def test_observation_is_evidence_not_causal_proof(observer, tmp_path):
    data = observer.observe()
    assert not data["errors"]
    assert data["metrics"]["memory_used_percent"] == 95
    assert data["metrics"]["cpu_online_count"] == 4
    assert data["metrics"]["disk_counters"]["sda"]["io_time_ms"] == 42
    assert data["services"]["status"] == "unknown"
    diagnoses = DiagnosticianAgent().diagnose(data)
    assert diagnoses
    assert all(d.confidence == 0 and "not causal proof" in d.cause for d in diagnoses)
    (observer.proc_root / "loadavg").write_text("nan invalid\n")
    (observer.proc_root / "meminfo").unlink()
    incomplete = observer.observe()
    assert "load_1m" not in incomplete["metrics"]
    assert "memory_used_percent" not in incomplete["metrics"]
    assert incomplete["sources"]["meminfo"]["status"] == "unknown"
    assert incomplete["services"]["status"] == "unknown"


def test_policy_unknown_mutations_and_confirmation_fail_closed(tmp_path):
    policy = AgentPolicyEngine(tmp_path / "policy.yaml", auto_load=False)
    policy.update_policy({"strict_whitelist": False})
    unknown = PlanStep("unknown", 1, "unregistered", "operator")
    assert not policy.evaluate_step(unknown, dry_run=True).allowed
    calls = []
    mutation_names = ["bdi_tune", "sysctl_tune", "drop_caches", "restart_service", "wal_checkpoint_passive"]
    for action in mutation_names:
        policy.set_action_tier(action, RiskTier.SAFE)
        policy.add_to_whitelist(action)
        step = PlanStep(action, 1, action, "fixture")
        decision = policy.evaluate_step(step, autonomous=True)
        assert decision.requires_confirmation and not decision.allowed
        assert decision.risk_tier != RiskTier.SAFE
        executor = ExecutorAgent(policy, {action: lambda s: calls.append(s)})
        assert not executor.execute(ExecutionPlan("p", steps=[step]), dry_run=False, autonomous=True)[0].success
    assert not calls
    # Even a policy returning allowed=True cannot bypass the confirmation blocker.
    policy.evaluate_step = lambda *a, **k: PolicyDecision(True, True, "confirmation", RiskTier.MODERATE)
    executor = ExecutorAgent(policy, {"restart_service": lambda s: calls.append(s)})
    executor.execute(ExecutionPlan("p", steps=[PlanStep("s", 1, "restart_service", "fixture")]), dry_run=False)
    assert not calls
    policy.evaluate_step = lambda *a, **k: PolicyDecision(True, False, "allowed", RiskTier.SAFE)
    executor = ExecutorAgent(policy, {"noop": lambda s: calls.append(s)})
    blocked = ExecutionPlan("blocked", steps=[PlanStep("unknown", 1, "missing", "fixture"),
                                             PlanStep("safe", 2, "noop", "fixture")])
    results = executor.execute(blocked, dry_run=False)
    assert len(results) == 1 and blocked.steps[1].status == StepStatus.PENDING
    assert not calls


def test_dry_run_persistence_and_bounds(observer, tmp_path):
    base = tmp_path / "agent"
    orchestrator = HAOSOrchestrator(base, observer=observer, max_cycles=1)
    record = orchestrator.run_cycle()
    assert record["outcome"] == "dry_run"
    assert record["plan"]["status"] == "draft"
    assert not record["verification"]["verified"]
    assert all(s["status"] == "skipped" for s in record["plan"]["steps"])
    assert all(not r["success"] for r in record["execution_results"])
    assert orchestrator.state.get_state()["cycle_id"] == record["cycle_id"]
    saved = json.loads((base / "plans" / f'{record["plan"]["plan_id"]}.json').read_text())
    assert saved == record
    assert record["cycle_id"] in (base / "memory/decisions.md").read_text()
    with pytest.raises(RuntimeError, match="budget"):
        orchestrator.run_cycle()
    calls = []
    executor = ExecutorAgent(handlers={"noop": lambda s: calls.append(s)}, max_steps=1)
    plan = ExecutionPlan("oversized", steps=[PlanStep(str(i), i, "noop", "fixture") for i in range(2)])
    with pytest.raises(ValueError, match="max_steps"):
        executor.execute(plan, dry_run=False)
    assert not calls


def test_storage_readings_are_risk_hypotheses(observer, tmp_path):
    usb = observer.sys_root / "devices/usb1/drive"
    usb.mkdir(parents=True)
    bus = observer.sys_root / "bus/usb"
    bus.mkdir(parents=True)
    (usb.parent / "subsystem").symlink_to(bus, target_is_directory=True)
    block = observer.sys_root / "block/sda"
    bdi = block / "bdi"
    bdi.mkdir(parents=True)
    (block / "device").symlink_to(usb, target_is_directory=True)
    (bdi / "max_ratio").write_text("100")
    (bdi / "strict_limit").write_text("0")
    (bdi / "max_bytes").write_text("67108864")
    sensor = observer.sys_root / "class/hwmon/hwmon0"
    sensor.mkdir(parents=True)
    (sensor / "name").write_text("drivetemp")
    (sensor / "temp1_input").write_text("75000")
    wal = tmp_path / "state.db-wal"
    with wal.open("wb") as file:
        file.truncate(65 * 1024 * 1024)
    observer.wal_paths = [wal, tmp_path / "absent-wal"]
    data = observer.observe()
    assert data["metrics"]["bdi"][0]["transport"] == "usb"
    assert data["metrics"]["drive_temperatures"][0]["celsius"] == 75
    assert data["metrics"]["wal_files"][1]["status"] == "unknown"
    findings = DiagnosticianAgent().diagnose(data)
    assert {"Large WAL file", "Drive temperature warning", "USB writeback budget risk"} <= {d.title for d in findings}
    assert all(d.confidence == 0 and d.cause.startswith("Unknown") for d in findings)


def test_malformed_policy_disables_execution_atomically(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"tiers":{"safe":null}}')
    policy = AgentPolicyEngine(path)
    step = PlanStep("s", 1, "recommend", "operator")
    assert not policy.evaluate_step(step).allowed
    assert not policy.evaluate_step(step, dry_run=True).allowed
    original = policy.to_dict()
    with pytest.raises(ValueError):
        policy.update_policy({"tiers": {"safe": None}})
    assert policy.to_dict() == original
    path.write_text('{}')
    assert policy.load_from_yaml()
    assert policy.evaluate_step(step).allowed


def test_registered_handler_failure_and_verification(tmp_path):
    policy = AgentPolicyEngine(tmp_path / "policy", auto_load=False)
    def handler(step):
        return ExecutionResult(step.step_id, True, "observation delivered")
    executor = ExecutorAgent(policy, {"noop": handler})
    plan = ExecutionPlan("p", steps=[PlanStep("s", 1, "noop", "fixture")])
    dry_results = executor.execute(plan)
    assert plan.status == PlanStatus.DRAFT
    assert plan.steps[0].status == StepStatus.SKIPPED
    assert not dry_results[0].success
    real_results = executor.execute(plan, dry_run=False)
    assert real_results[0].success
    assert not VerifierAgent().verify(plan, real_results, {}, {}).verified
    def failing(step):
        raise RuntimeError("test failure")
    executor.handlers["noop"] = failing
    plan.steps.append(PlanStep("second", 2, "noop", "fixture"))
    results = executor.execute(plan, dry_run=False)
    assert len(results) == 1
    assert plan.status == PlanStatus.FAILED
    assert "test failure" in results[0].error
