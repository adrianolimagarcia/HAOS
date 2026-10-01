import tempfile
import threading
import time
from pathlib import Path
import pytest

from hermes.platform.execution.mission_runtime import MissionSupervisor
from hermes.platform.execution.mission_store import MissionStore
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.tasks.spec import TaskSpec


def test_mission_supervisor_topological_compile_and_execution():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "kanban.db"
        home_path = Path(tmpdir) / "home"
        home_path.mkdir(parents=True, exist_ok=True)
        store = MissionStore(db_path, profile_key="default", home_path=str(home_path))
        adapter = KanbanAdapter(db_path=db_path)

        executed_tasks = []

        def mock_executor(spec: TaskSpec, cancel: threading.Event):
            executed_tasks.append(spec.id)
            return {"summary": f"Completed {spec.id}", "evidence": {"task": spec.id}}

        supervisor = MissionSupervisor(
            store=store,
            adapter=adapter,
            profile_key="default",
            home_path=home_path,
            executor=mock_executor,
            poll_seconds=0.05,
        )

        mission = {
            "id": "m-test-1",
            "title": "Topological Mission",
            "goal": "Test DAG execution",
            "revision": 1,
            "workflow": {
                # Notice node B is listed BEFORE node A, but B depends on A!
                "nodes": [
                    {"id": "node-b", "action": "Step B", "role": "agent"},
                    {"id": "node-a", "action": "Step A", "role": "agent"},
                ],
                "edges": [
                    {"from_node": "node-a", "to_node": "node-b"},
                ],
            },
        }

        # Start mission
        start_res = supervisor.start(mission)
        assert start_res["status"] == "RUNNING"
        assert len(start_res["task_ids"]) == 2

        # Wait for both tasks to execute and mission to complete
        deadline = time.time() + 5.0
        while time.time() < deadline:
            state = store.get("m-test-1")
            if state and state.get("actual_state") == "completed":
                break
            time.sleep(0.05)

        supervisor.shutdown()

        # Both tasks executed in proper topological order: A first, then B!
        assert len(executed_tasks) == 2
        assert executed_tasks[0] == "civ-m-test-1-node-a"
        assert executed_tasks[1] == "civ-m-test-1-node-b"

        # Check mission state in store
        m_state = store.get("m-test-1")
        assert m_state["actual_state"] == "completed"


def test_mission_supervisor_cooperative_pause_and_resume():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "kanban.db"
        home_path = Path(tmpdir) / "home"
        home_path.mkdir(parents=True, exist_ok=True)
        store = MissionStore(db_path, profile_key="default", home_path=str(home_path))
        adapter = KanbanAdapter(db_path=db_path)

        cancelled_flag = threading.Event()
        started_flag = threading.Event()

        def slow_executor(spec: TaskSpec, cancel: threading.Event):
            started_flag.set()
            # Wait up to 2 seconds for cancellation
            for _ in range(20):
                if cancel.is_set():
                    cancelled_flag.set()
                    return {"summary": "Cancelled"}
                time.sleep(0.05)
            return {"summary": "Finished normally"}

        supervisor = MissionSupervisor(
            store=store,
            adapter=adapter,
            profile_key="default",
            home_path=home_path,
            executor=slow_executor,
            poll_seconds=0.05,
        )

        mission = {
            "id": "m-pause-1",
            "title": "Pausable Mission",
            "workflow": {
                "nodes": [{"id": "slow-node", "action": "Slow Step"}],
                "edges": [],
            },
        }

        supervisor.start(mission)
        assert started_flag.wait(timeout=2.0)

        # Pause mission
        pause_res = supervisor.pause("m-pause-1")
        assert pause_res["desired_state"] == "paused"

        # Verify task was cooperatively cancelled
        assert cancelled_flag.wait(timeout=2.0)

        supervisor.shutdown()


def test_mission_supervisor_no_executor_does_not_claim():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "kanban.db"
        home_path = Path(tmpdir) / "home"
        home_path.mkdir(parents=True, exist_ok=True)
        store = MissionStore(db_path, profile_key="default", home_path=str(home_path))
        adapter = KanbanAdapter(db_path=db_path)

        supervisor = MissionSupervisor(
            store=store,
            adapter=adapter,
            profile_key="default",
            home_path=home_path,
            executor=None,  # No executor
            poll_seconds=0.05,
        )

        mission = {
            "id": "m-no-exec",
            "title": "No Executor Mission",
            "workflow": {
                "nodes": [{"id": "node-1", "action": "Action 1"}],
                "edges": [],
            },
        }

        compiled = supervisor.compile(mission)
        task_id = compiled[0].task_id

        # Verify initial status is ready
        task_before = adapter.get_task(task_id)
        assert task_before["status"].lower() in ("ready", "triage")

        # Start supervisor
        supervisor.start(mission)
        time.sleep(0.2)
        supervisor.shutdown()

        # Task was NEVER claimed into running state without executor
        task_after = adapter.get_task(task_id)
        assert task_after["status"].lower() in ("ready", "triage")
        assert task_after["claim_lock"] is None


def test_mission_supervisor_failure_handling_and_recovery():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "kanban.db"
        home_path = Path(tmpdir) / "home"
        home_path.mkdir(parents=True, exist_ok=True)
        store = MissionStore(db_path, profile_key="default", home_path=str(home_path))
        adapter = KanbanAdapter(db_path=db_path)

        def failing_executor(spec: TaskSpec, cancel: threading.Event):
            raise RuntimeError("intentional test crash")

        supervisor = MissionSupervisor(
            store=store,
            adapter=adapter,
            profile_key="default",
            home_path=home_path,
            executor=failing_executor,
            poll_seconds=0.05,
        )

        mission = {
            "id": "m-fail-1",
            "title": "Failing Mission",
            "workflow": {
                "nodes": [{"id": "bad-node", "action": "Crash"}],
                "edges": [],
            },
        }

        supervisor.start(mission)

        # Wait for task failure to be recorded
        deadline = time.time() + 3.0
        task_id = "civ-m-fail-1-bad-node"
        failed = False
        while time.time() < deadline:
            task = adapter.get_task(task_id)
            if task and task.get("consecutive_failures", 0) > 0:
                failed = True
                break
            time.sleep(0.05)

        supervisor.shutdown()
        assert failed

        # Test recovery
        # Manually create another running mission in store
        store.create_or_import("m-recovered", title="Recovered Mission", desired_state="running", actual_state="running")
        recovered = supervisor.recover_active_missions()
        assert "m-recovered" in recovered
        supervisor.shutdown()
