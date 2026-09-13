import os
import tempfile
import unittest
from pathlib import Path

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.execution.dispatcher import HAOSDispatcher
from hermes.platform.execution.lane_executor import (
    HermesCliLaneWorker,
    LaneUnavailableError,
    lane_for_spec,
)

from hermes_cli import kanban_db_dispatch as kbd


class TestK1Dispatcher(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        # Workspace/dispatch roots MUST stay under the temp dir (no ~/.hermes).
        self._env_kanban = os.environ.get("HERMES_KANBAN_HOME")
        self._env_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
        os.environ["HERMES_HOME"] = str(root / "hermes-home")
        self.db = root / "kanban.db"
        self.adapter = KanbanAdapter(self.db)
        self.dispatcher = HAOSDispatcher(self.adapter)

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

    def _save(self, spec, **kw):
        return self.adapter.save_task(spec, status="READY", **kw)

    # ------------------------------------------------------------------ #
    def test_lane_for_spec_pure(self):
        self.assertEqual(lane_for_spec({"required_capabilities": ["git"]}), "kilo")
        self.assertEqual(lane_for_spec({"workspace_type": "git_worktree"}), "kilo")
        self.assertEqual(lane_for_spec({"required_capabilities": ["web"]}), "hermes")

    def test_claim_tick_executes_ready_card_e2e(self):
        tid = self._save(TaskSpec(id="T-D1", title="Git task", goal="g",
                                  workspace_type="scratch",
                                  required_capabilities=["git", "code-intelligence"]))
        executed = self.dispatcher.claim_tick(worker_id="haos-worker")
        self.assertEqual(executed, [tid])

        t = self.adapter.get_task(tid)
        self.assertEqual(t["status"], "done")

        # Resultado HAOS com evidência de lane + run canônico encerrado.
        result = self.adapter.get_result(tid)
        self.assertEqual(result.evidence["lane"], "kilo")
        run = self.adapter.get_run(tid)
        self.assertEqual(run.status, "ended")
        self.assertIsNotNone(run.run_id)

        # Seam K1: o run HAOS referencia o run canônico do kernel — mesmo id em
        # task_runs (status done / outcome completed / summary da lane).
        conn = self.adapter._connect()
        canonical = conn.execute(
            "SELECT id, status, outcome, summary FROM task_runs WHERE task_id = ?", (tid,)
        ).fetchone()
        self.assertIsNotNone(canonical)
        self.assertEqual(run.run_id, canonical["id"])
        self.assertEqual(canonical["status"], "done")
        self.assertEqual(canonical["outcome"], "completed")
        self.assertIn("canonical workspace", canonical["summary"])

        # A lane registrou o workspace canônico (scratch sob o root do board)
        # na evidência — após o complete o scratch é arquivado pelo kernel.
        home = Path(os.environ["HERMES_KANBAN_HOME"])
        ws_expected = str(home / "kanban" / "workspaces" / tid)
        self.assertTrue(result.evidence["workspace"].startswith(ws_expected))

    def test_scratch_workspace_exists_during_execution(self):
        """O workspace canônico existe e recebe o marcador da lane DURANTE a
        execução; o complete upstream arquiva o scratch depois."""
        from hermes.platform.execution.lane_executor import DeterministicLaneWorker
        from hermes_cli import kanban_db as kb
        from hermes_cli import kanban_db_workspace as kbw

        tid = self._save(TaskSpec(id="T-WS", title="Ws", goal="g", workspace_type="scratch"))
        self.assertTrue(self.adapter.claim_task(tid, "w-1"))
        conn = self.adapter._connect()
        task = kb.get_task(conn, tid)
        ws = kbw.resolve_workspace(task, board=None)
        self.assertTrue(ws.is_dir(), f"workspace {ws} deve existir após resolve")

        spec = self.adapter.get_task(tid)["spec"]
        out = DeterministicLaneWorker().execute(tid, ws, spec)
        marker = ws / ".haos" / "run.json"
        self.assertTrue(marker.exists())
        self.assertEqual(out["evidence"]["lane"], "hermes")  # spec sem cap git

        # Complete: kernel persiste resultado e arquiva o scratch.
        self.adapter.complete_task(tid, summary=out["summary"])

    def test_claim_tick_honors_max_spawn(self):
        a = self._save(TaskSpec(id="T-A1", title="A", goal="g", workspace_type="scratch"))
        b = self._save(TaskSpec(id="T-A2", title="B", goal="g", workspace_type="scratch"))
        executed = self.dispatcher.claim_tick(max_spawn=1)
        self.assertEqual(len(executed), 1)
        self.assertIn(executed[0], {a, b})
        done = ({a, b} - set(executed)).pop()
        self.assertEqual(self.adapter.get_task(done)["status"], "ready")

    def test_claim_tick_fan_in_runs_child_after_parent_done(self):
        pid = self._save(TaskSpec(id="T-P1", title="Parent", goal="p", workspace_type="scratch"))
        cid = self._save(TaskSpec(id="T-C1", title="Child", goal="c",
                                  workspace_type="scratch", requires_tasks=["T-P1"]))
        # Filho começa como `todo` (parent ainda não done).
        self.assertEqual(self.adapter.get_task(cid)["status"], "todo")
        # Um tick com orçamento 1 executa só o parent; o complete upstream
        # promove o filho a `ready` (fan-in), mas o loop já terminou.
        executed = self.dispatcher.claim_tick(max_spawn=1)
        self.assertEqual(executed, [pid])
        self.assertEqual(self.adapter.get_task(pid)["status"], "done")
        # Tick seguinte executa o filho promovido.
        executed2 = self.dispatcher.claim_tick(max_spawn=1)
        self.assertEqual(executed2, [cid])
        self.assertEqual(self.adapter.get_task(cid)["status"], "done")

    # ------------------------------------------------------------------ #
    def test_dispatch_tick_spawns_via_spawn_fn_without_profile_gate(self):
        # Board SEM gate de perfis: o tick do upstream claima e chama o
        # spawn_fn do HAOS (workspace canônico + complete) — emulação do
        # ambiente em que o dispatcher não roda com perfis Hermes.
        orig_gate = kbd._profile_exists_fn
        orig_resolve = kbd._resolve_default_assignee
        kbd._profile_exists_fn = lambda: None
        kbd._resolve_default_assignee = lambda name: name
        try:
            tid = self._save(TaskSpec(id="T-DP", title="Dispatch", goal="g",
                                      workspace_type="scratch",
                                      required_capabilities=["git"]))
            result = self.dispatcher.dispatch_tick(default_assignee="haos-worker")
            self.assertEqual(len(result.spawned), 1)
            self.assertEqual(result.spawned[0][0], tid)
            self.assertEqual(self.adapter.get_task(tid)["status"], "done")
            self.assertEqual(self.adapter.get_result(tid).evidence["lane"], "kilo")
        finally:
            kbd._profile_exists_fn = orig_gate
            kbd._resolve_default_assignee = orig_resolve

    def test_dispatch_tick_unassigned_and_lane_assignee_semantics(self):
        # Ambiente real com gate de perfis:
        #  - card sem assignee -> skipped_unassigned (default_assignee não é
        #    perfil Hermes => resolvido para None por design);
        #  - card com assignee de lane (não-perfil) -> skipped_nonspawnable.
        unassigned = self._save(TaskSpec(id="T-G1", title="U", goal="g", workspace_type="scratch"))
        lane_card = self._save(TaskSpec(id="T-G2", title="L", goal="g", workspace_type="scratch"),
                               assignee="haos-kilo")
        result = self.dispatcher.dispatch_tick(default_assignee="haos-worker")
        self.assertEqual(result.spawned, [])
        self.assertIn(unassigned, result.skipped_unassigned)
        self.assertIn(lane_card, result.skipped_nonspawnable)
        # Control-plane da lane ainda executa esses cards via claim_tick.
        executed = self.dispatcher.claim_tick(max_spawn=2)
        self.assertEqual(set(executed), {unassigned, lane_card})
        for tid in executed:
            self.assertEqual(self.adapter.get_task(tid)["status"], "done")

    def test_hermes_cli_lane_requires_runtime(self):
        lane = HermesCliLaneWorker(hermes_command="/nonexistent/hermes")
        self.assertFalse(lane.available())
        with self.assertRaises(LaneUnavailableError):
            lane.execute("t_1", Path("/tmp/ws"), {})

    def test_claim_tick_with_concurrency_guard_pre_claim(self):
        from hermes.platform.execution.backpressure import ConcurrencyGuard
        # Guard configurado com limite global = 1
        guard = ConcurrencyGuard(max_global_concurrency=1)
        dispatcher_with_guard = HAOSDispatcher(self.adapter, concurrency_guard=guard)

        t1 = self._save(TaskSpec(id="T-BP1", title="Task 1", goal="g", workspace_type="scratch"))
        t2 = self._save(TaskSpec(id="T-BP2", title="Task 2", goal="g", workspace_type="scratch"))

        # Executa tick com max_spawn=2, mas guard admite 1 por vez sequencialmente (release no finally)
        executed = dispatcher_with_guard.claim_tick(max_spawn=2)
        self.assertEqual(len(executed), 2)
        self.assertEqual(guard.active_workers_count, 0) # liberado no finally!

        # Se simularmos ocupação total do guard antes do claim_tick:
        guard.acquire("fake_task")
        t3 = self._save(TaskSpec(id="T-BP3", title="Task 3", goal="g", workspace_type="scratch"))
        blocked_executed = dispatcher_with_guard.claim_tick(max_spawn=1)
        self.assertEqual(blocked_executed, [])

        # O card T-BP3 permanece intacto com status='ready' e claim_lock=NULL (sem queimar retries)
        t3_data = self.adapter.get_task(t3)
        self.assertEqual(t3_data["status"], "ready")
        self.assertIsNone(t3_data["claim_lock"])
        self.assertEqual(t3_data["consecutive_failures"], 0)

        # Liberando o guard, o card executa normalmente
        guard.release("fake_task")
        executed_after = dispatcher_with_guard.claim_tick(max_spawn=1)
        self.assertEqual(executed_after, [t3])
        self.assertEqual(self.adapter.get_task(t3)["status"], "done")


if __name__ == "__main__":
    unittest.main()
