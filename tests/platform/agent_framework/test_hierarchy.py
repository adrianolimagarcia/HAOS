"""Behavioral contracts for bounded operational specialist execution."""
import asyncio

import pytest

from hermes.platform.agent_framework.hierarchy import HierarchicalDiagnostician, HierarchyExecutionError
from hermes.platform.agent_framework.models import Diagnosis, EvidenceItem
from hermes.platform.agent_framework.pipeline import HAOSOrchestrator, ObserverAgent
from hermes.platform.tasks.spec import TaskSpec


def task(key, domain, dependencies=()):
    return TaskSpec(id=key, title=key, goal="analyze", posture=domain,
                    requires_tasks=list(dependencies))


def finding(key):
    return Diagnosis(key, key, "Unknown", 0, [EvidenceItem("observed", 1)])


def test_parallel_admission_dependency_scope_and_deterministic_results():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        peak = 0
        seen = []

        async def worker(context):
            nonlocal active, peak
            assert not context.task.allow_delegation and not context.task.allow_child_tasks
            assert context.task.max_child_tasks == context.task.max_cost_usd == 0
            active += 1
            peak = max(peak, active)
            seen.append(context.task.id)
            if active == 2:
                entered.set()
            assert set(context.telemetry["metrics"]) <= {
                "storage": {"wal_files"}, "memory": {"memory_used_percent"}, "system": {"load_1m"}
            }[context.task.posture]
            if context.task.id == "review":
                assert set(context.dependencies) == {"first", "second"}
                assert context.dependencies["first"][0].issue_id == "first"
            else:
                await release.wait()
            active -= 1
            return [finding(context.task.id)]

        supervisor = HierarchicalDiagnostician({d: worker for d in ("storage", "memory", "system")}, max_parallel=2)
        specs = [task("first", "storage"), task("second", "memory"),
                 task("third", "system"), task("review", "system", ("first", "second"))]
        running = asyncio.create_task(supervisor.run(
            {"metrics": {"wal_files": [], "memory_used_percent": 95, "load_1m": 4, "secret": "hidden"}}, tasks=specs))
        await asyncio.wait_for(entered.wait(), 3)
        assert seen == ["first", "second"]
        release.set()
        result = await running
        assert peak == 2
        assert result.status == "completed"
        assert [d.issue_id for d in result.diagnoses] == [t.id for t in specs]
        assert specs[0].allow_delegation  # Input TaskSpecs remain unchanged.
    asyncio.run(scenario())


def test_failure_blocks_dependents_but_independent_worker_completes():
    async def worker(context):
        if context.task.id == "bad":
            raise ValueError("broken source")
        assert context.task.id != "blocked"
        return [finding(context.task.id)]
    supervisor = HierarchicalDiagnostician({"system": worker})
    result = asyncio.run(supervisor.run({}, tasks=[task("bad", "system"),
        task("blocked", "system", ("bad",)), task("good", "system")]))
    assert result.status == "failed"
    assert [r.status for r in result.task_results] == ["failed", "blocked", "completed"]
    assert result.task_results[0].error == "ValueError: broken source"
    assert [d.issue_id for d in result.diagnoses] == ["good"]


@pytest.mark.parametrize("external", [False, True])
def test_cancellation_drains_workers_without_starting_pending_tasks(external):
    async def scenario():
        entered = asyncio.Event()
        drained = asyncio.Event()
        cancel = asyncio.Event()
        seen = []
        async def worker(context):
            seen.append(context.task.id)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()
        supervisor = HierarchicalDiagnostician({"system": worker}, max_parallel=1)
        running = asyncio.create_task(supervisor.run({}, tasks=[task("running", "system"),
            task("pending", "system")], cancel_event=cancel))
        await asyncio.wait_for(entered.wait(), 3)
        if external:
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        else:
            cancel.set()
            result = await running
            assert result.status == "cancelled"
            assert all(r.status == "cancelled" for r in result.task_results)
        assert drained.is_set()
        assert seen == ["running"]
    asyncio.run(scenario())


@pytest.mark.parametrize("total", [False, True])
def test_timeouts_are_visible_and_worker_cleanup_runs(total):
    async def scenario():
        drained = asyncio.Event()
        async def worker(context):
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()
        supervisor = HierarchicalDiagnostician({"system": worker},
            worker_timeout=3 if total else 0.03, total_timeout=0.03 if total else 3)
        result = await supervisor.run({}, tasks=[task("slow", "system"), task("child", "system", ("slow",))])
        assert result.status == "timed_out"
        assert result.task_results[0].status == "timed_out"
        assert result.task_results[1].status == ("timed_out" if total else "blocked")
        assert drained.is_set()
    asyncio.run(scenario())


@pytest.mark.parametrize("specs", [
    [task("x", "system", ("missing",))],
    [task("x", "system", ("y",)), task("y", "system", ("x",))],
    [task("x", "system"), task("x", "system")],
    [task("x", "unknown")],
])
def test_invalid_graph_fails_before_worker_admission(specs):
    async def worker(context):
        pytest.fail("invalid graph executed")
    supervisor = HierarchicalDiagnostician({"system": worker})
    with pytest.raises(ValueError):
        asyncio.run(supervisor.run({}, tasks=specs))


def test_budgets_and_invalid_worker_outputs_fail_closed():
    async def output(context):
        return [finding("one"), finding("two")]
    supervisor = HierarchicalDiagnostician({"system": output}, max_tasks=1, max_findings=1)
    with pytest.raises(ValueError, match="budget"):
        asyncio.run(supervisor.run({}, tasks=[task("x", "system"), task("y", "system")]))
    result = asyncio.run(supervisor.run({}))
    assert result.status == "failed"
    assert "budget" in result.task_results[0].error
    assert not result.diagnoses

    async def no_evidence(context):
        return [Diagnosis("x", "x", "Unknown", 0)]
    result = asyncio.run(HierarchicalDiagnostician({"system": no_evidence}).run({}))
    assert result.status == "failed"
    assert "evidence" in result.task_results[0].error


def test_real_observation_to_hierarchical_pipeline_in_temporary_profile(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proc, sys = tmp_path / "proc", tmp_path / "sys"
    (proc / "sys/vm").mkdir(parents=True)
    (sys / "devices/system/cpu").mkdir(parents=True)
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 20 kB\nDirty: 5 kB\nWriteback: 0 kB\n")
    (proc / "loadavg").write_text("4.0 2.0 1.0 1/3 1\n")
    (proc / "diskstats").write_text("8 0 sda 1 0 0 0 1 0 0 0 0 0 0\n")
    (proc / "sys/vm/dirty_ratio").write_text("20\n")
    (sys / "devices/system/cpu/online").write_text("0-1\n")
    wal = tmp_path / "state.db-wal"
    with wal.open("wb") as stream:
        stream.truncate(65 * 1024 * 1024)
    supervisor = HierarchicalDiagnostician()
    pipeline = HAOSOrchestrator(base_dir=home / "agent", observer=ObserverAgent(proc, sys, [wal]))
    pipeline.diagnostician = supervisor
    result = pipeline.run_cycle()
    assert supervisor.last_result.status == "completed"
    assert {d.issue_id for d in supervisor.last_result.diagnoses} >= {
        "hypothesis_memory_used_percent", "hypothesis_load_1m", "wal_growth_0"}
    assert all(d.confidence == 0 and d.evidence for d in supervisor.last_result.diagnoses)
    assert all("Unknown" in d.cause for d in supervisor.last_result.diagnoses)
    assert result["diagnoses"]
    assert home.exists()


def test_sync_failure_exposes_receipt_and_nested_loop_requires_async_api():
    async def broken(context):
        raise RuntimeError("specialist unavailable")
    supervisor = HierarchicalDiagnostician({"system": broken})
    with pytest.raises(HierarchyExecutionError) as caught:
        supervisor.diagnose({})
    assert caught.value.result is supervisor.last_result
    assert supervisor.last_result.to_dict()["task_results"][0]["status"] == "failed"

    async def scenario():
        with pytest.raises(RuntimeError, match="event loop"):
            supervisor.diagnose({})
    asyncio.run(scenario())


def test_unknown_sources_are_domain_scoped_and_no_cause_is_invented():
    supervisor = HierarchicalDiagnostician()
    result = asyncio.run(supervisor.run({"errors": {"meminfo": "not available"}, "metrics": {}}))
    assert result.status == "completed"
    assert [d.issue_id for d in result.diagnoses] == ["telemetry_unknown_memory"]
    assert result.diagnoses[0].confidence == 0
    assert not result.diagnoses[0].cause.startswith("Memory")
