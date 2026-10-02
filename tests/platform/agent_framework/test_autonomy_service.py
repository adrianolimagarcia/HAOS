"""Real durable runtime with fake inference; no provider or production writes."""
import json
import threading
import pytest

from hermes.platform.agent_framework.autonomy_config import AutonomyConfig
from hermes.platform.agent_framework.autonomy_service import AutonomyService
from hermes.platform.agent_framework.models import HAOSEvent
from hermes.platform.agent_framework.pipeline import HAOSOrchestrator, ExecutorAgent


class Observer:
    def observe(self):
        return {"metrics": {}, "errors": {}}


class Investigator:
    def __init__(self):
        self.calls = 0
        self.callback = None

    def investigate(self, event, telemetry):
        self.calls += 1
        if self.callback:
            self.callback()
        return {"summary": "Investigated", "findings": [{"title": "Warning", "cause": "Unknown",
                 "confidence": 0, "recommendations": ["Inspect before changing anything"]}]}


def service(home, **kwargs):
    return AutonomyService(home / "agent", investigator=kwargs.pop("investigator", Investigator()),
                           observer=Observer(), config=AutonomyConfig(enabled=True, **kwargs))


def test_singleton_pause_and_saved_pure_plan(tmp_path):
    runtime = service(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            service(tmp_path)
        runtime.queue.submit(HAOSEvent("test.failed", "test"))
        runtime.queue.set_pause(True)
        assert runtime.tick()["status"] == "paused"
        assert runtime.investigator.calls == 0
        runtime.queue.set_pause(False)
        result = runtime.tick()
        assert result["outcome"] == "pending_authorization"
        assert (tmp_path / "agent" / "plans" / (result["plan_id"] + ".json")).exists()
        assert not (tmp_path / "agent" / "artifacts" / "operational-report.json").exists()
        heartbeat = json.loads((tmp_path / "agent" / "autonomy" / "service.json").read_text())
        assert heartbeat["active_job_id"] is None
    finally:
        runtime.close()


def test_pause_after_inference_and_recovered_claim_never_mutate(tmp_path):
    investigator = Investigator()
    runtime = service(tmp_path, investigator=investigator, auto_apply=True)
    try:
        runtime.queue.submit(HAOSEvent("test.failed", "test", payload={"issue_id": "first"}))
        investigator.callback = lambda: runtime.queue.set_pause(True)
        assert runtime.tick()["outcome"] == "pending_authorization"
        assert not (tmp_path / "agent" / "artifacts" / "operational-report.json").exists()
        runtime.queue.set_pause(False)
        runtime.queue.submit(HAOSEvent("test.failed", "test", payload={"issue_id": "second"}))
        job = runtime.queue.claim("crashed")
        runtime.queue._db.execute("UPDATE jobs SET lease_until=0 WHERE job_id=?", (job["job_id"],))
        assert runtime.tick()["status"] == "uncertain"
        assert investigator.calls == 1
    finally:
        runtime.close()


def test_exact_grant_is_required_and_never_created(tmp_path, monkeypatch):
    runtime = service(tmp_path, auto_apply=True)
    try:
        runtime.queue.submit(HAOSEvent("test.failed", "test"))
        result = runtime.tick()
        orchestrator = HAOSOrchestrator(tmp_path / "agent", observer=Observer())
        plan = orchestrator.load_plan(result["plan_id"])
        assert not orchestrator.executor.policy.action_grant(plan.steps[0], autonomous=True)
        assert not (tmp_path / "agent" / "agent-policy.yaml").exists()
        # A grant must match the future artifact content exactly, not just its target.
        orchestrator.grant(plan.plan_id, "report", autonomous=True)
        runtime.queue.submit(HAOSEvent("test.failed", "test", payload={"issue_id": "different"}))
        assert runtime.tick()["outcome"] == "pending_authorization"
        assert not (tmp_path / "agent" / "artifacts" / "operational-report.json").exists()
        runtime.queue.clock = lambda: 9999999999
        runtime.queue.submit(HAOSEvent("test.failed", "test"))
        applied = runtime.tick()
        assert applied["outcome"] == "verified"
        assert (tmp_path / "agent" / "artifacts" / "operational-report.json").is_file()
    finally:
        runtime.close()



@pytest.mark.parametrize("block", ["pause", "revoke"])
def test_mutation_guard_rechecks_after_execution_lock(tmp_path, monkeypatch, block):
    runtime = service(tmp_path, auto_apply=True)
    try:
        runtime.queue.submit(HAOSEvent("test.failed", "test"))
        initial = runtime.tick()
        orchestrator = HAOSOrchestrator(tmp_path / "agent", observer=Observer())
        orchestrator.grant(initial["plan_id"], "report", autonomous=True)
        runtime.queue.clock = lambda: 9999999999
        runtime.queue.submit(HAOSEvent("test.failed", "test"))
        arrived = threading.Event()
        original = ExecutorAgent.execute
        def execute(executor, *args, **kwargs):
            if kwargs.get("autonomous"):
                arrived.set()
            return original(executor, *args, **kwargs)
        monkeypatch.setattr(ExecutorAgent, "execute", execute)
        outputs = []
        with runtime.state.acquire_lock("action-execution"):
            thread = threading.Thread(target=lambda: outputs.append(runtime.tick()))
            thread.start()
            assert arrived.wait(3)
            if block == "pause":
                runtime.queue.set_pause(True)
            else:
                orchestrator.executor.policy.update_policy({"action_grants": []}, persist=True)
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert outputs
        assert not (tmp_path / "agent" / "artifacts" / "operational-report.json").exists()
    finally:
        runtime.close()

def test_error_dashboard_has_type_only_and_profile_isolation(tmp_path):
    class Broken(Investigator):
        def investigate(self, event, telemetry):
            raise RuntimeError("secret-provider-key")
    first = service(tmp_path / "a", investigator=Broken())
    second = service(tmp_path / "b")
    try:
        first.queue.submit(HAOSEvent("test.failed", "test"))
        result = first.tick()
        assert result["error"] == "RuntimeError"
        assert "secret-provider-key" not in json.dumps(first.queue.recent())
        assert second.tick()["status"] == "idle"
        assert second.investigator.calls == 0
    finally:
        first.close()
        second.close()
