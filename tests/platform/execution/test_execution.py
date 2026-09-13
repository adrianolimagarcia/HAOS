import os
import tempfile
import unittest
from pathlib import Path

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.execution.scheduler import HAOSScheduler
from hermes.platform.execution.spawn_resolver import SpawnResolver
from hermes.platform.adapters.kilo.adapter import KiloLaneAdapter
from hermes.platform.workspaces.manager import WorkspaceError

class TestExecutionAndKilo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env_kanban = os.environ.get("HERMES_KANBAN_HOME")
        self._env_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
        os.environ["HERMES_HOME"] = str(root / "hermes-home")
        self.db = root / "kanban.db"
        self.adapter = KanbanAdapter(self.db)

    def tearDown(self):
        self.adapter.close()
        if self._env_kanban is None:
            os.environ.pop("HERMES_KANBAN_HOME", None)
        else:
            os.environ["HERMES_KANBAN_HOME"] = self._env_kanban
        if self._env_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._env_home
        self._tmp.cleanup()

    def test_scheduler_and_spawn_resolution(self):
        t1 = TaskSpec(id="T-1", title="Normal priority", goal="Normal", priority=30)
        t2 = TaskSpec(id="T-2", title="Critical priority", goal="Critical", priority=90, required_capabilities=["git"])

        scheduler = HAOSScheduler()
        assignment = scheduler.schedule_next([t1, t2], critical_path_ids=["T-2"])

        self.assertEqual(assignment.task_id, "T-2")
        self.assertEqual(assignment.lane, "kilo")
        self.assertEqual(assignment.execution_shape, "worker_lane")

    def test_kilo_adapter(self):
        # K8: KiloLaneAdapter resolve o workspace CANÔNICO do card (não dir /tmp avulso).
        t = TaskSpec(id="T-3", title="Kilo Task", goal="Coding",
                     required_capabilities=["git"], workspace_type="scratch")
        tid = self.adapter.save_task(t, status="READY")
        assignment = SpawnResolver().resolve(t)

        adapter = KiloLaneAdapter(db_path=str(self.db))
        res = adapter.execute_assignment(assignment)

        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["lane"], "kilo")
        home = Path(os.environ["HERMES_KANBAN_HOME"])
        self.assertIn(str(home / "kanban" / "workspaces" / tid), res["workspace_path"])
        self.assertNotIn("haos_workspace_", res["workspace_path"])

    def test_kilo_adapter_requires_saved_card(self):
        # Sem card canônico (save_task) a lane real não inventa workspace.
        t = TaskSpec(id="T-99", title="Ghost", goal="g", workspace_type="scratch")
        assignment = SpawnResolver().resolve(t)
        adapter = KiloLaneAdapter(db_path=str(self.db))
        with self.assertRaises(WorkspaceError):
            adapter.execute_assignment(assignment)

    def test_model_precedence_task_binding_overrides_posture(self):
        """HAOS v1.1: explicit task model_profile > posture model binding."""
        # Architect posture binds architecture-primary...
        t_arch = TaskSpec(id="T-10", title="Arch task", goal="Design", posture="architect")
        a_arch = SpawnResolver().resolve(t_arch)
        self.assertEqual(a_arch.model_profile_id, "architecture-primary")
        self.assertEqual(a_arch.resolved_model_family, "claude-3-5")

        # ...but an explicit task override wins.
        t_override = TaskSpec(id="T-11", title="Arch task w/ override", goal="Design",
                              posture="architect", model_profile="coding-primary")
        a_override = SpawnResolver().resolve(t_override)
        self.assertEqual(a_override.model_profile_id, "coding-primary")

    def test_assignment_snapshot_contains_full_route_chain(self):
        t = TaskSpec(id="T-12", title="Snapshot", goal="Check snapshot", posture="implementer")
        a = SpawnResolver().resolve(t)
        # Full ordered route chain (not just the selected hop) for reproducibility.
        self.assertEqual(len(a.provider_routes), 2)
        self.assertEqual(a.provider_routes[0]["priority"], 1)
        self.assertIsNotNone(a.selected_route)
        self.assertIn("skills_snapshot", a.__dict__)

if __name__ == "__main__":
    unittest.main()
