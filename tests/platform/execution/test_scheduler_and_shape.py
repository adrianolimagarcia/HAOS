"""A4 — Scheduler (backpressure/fairness) e decisão de shape (reuso) invariantes.

Contratos:
(a) pick_with_fairness: maior score sempre vence; empate no topo rotaciona
    pelo último agendado (anti-starvation, determinístico).
(b) HAOSScheduler: running_count >= max_concurrency -> None com reason
    "backpressure" (≠ "no_ready"); abaixo do teto resolve de novo.
(c) classify_execution: default preserva a heurística histórica (paridade com
    lane_for_spec); git domina qualquer reuse; reuse persistent/orchestrator
    muda shape/modo na lane hermes; reuse inválido falha rápido.
(d) SpawnResolver aplica reuse; TaskSpec default "none"; round-trip persiste.
"""

import os
import tempfile
import unittest
from pathlib import Path

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.execution.scheduler import HAOSScheduler, pick_with_fairness
from hermes.platform.execution.spawn_resolver import SpawnResolver
from hermes.platform.execution.classify import (
    classify_execution, InvalidReuseError,
    LANE_HERMES, LANE_KILO,
    SHAPE_EPHEMERAL, SHAPE_PERSISTENT, SHAPE_SUB_ORCH, SHAPE_WORKER_LANE,
)
from hermes.platform.execution.lane_executor import lane_for_spec


class TestFairness(unittest.TestCase):
    def test_unique_top_score_wins(self):
        scored = [("a", 1.0), ("b", 50.5), ("c", 10.0)]
        self.assertEqual(pick_with_fairness(scored, None), "b")

    def test_tie_rotates_away_from_last_scheduled(self):
        scored = [("a", 5.0), ("b", 5.0)]
        self.assertEqual(pick_with_fairness(scored, None), "a")
        self.assertEqual(pick_with_fairness(scored, "a"), "b")
        self.assertEqual(pick_with_fairness(scored, "b"), "a")

    def test_backpressure_and_reasons(self):
        t = TaskSpec(id="T-1", title="t", goal="g", workspace_type="scratch")
        sched = HAOSScheduler(max_concurrency=2)
        self.assertIsNone(sched.schedule_next([t], running_count=2))
        self.assertEqual(sched.last_reject_reason, "backpressure")
        self.assertIsNone(sched.schedule_next([]))
        self.assertEqual(sched.last_reject_reason, "no_ready")
        a = sched.schedule_next([t], running_count=1)
        self.assertIsNotNone(a)
        self.assertEqual(a.lane, LANE_HERMES)
        self.assertEqual(sched.last_reject_reason, "")

    def test_critical_path_and_priority_inheritance(self):
        from hermes.platform.execution.scheduler import (
            compute_deterministic_critical_path,
            compute_inherited_priorities,
        )
        from hermes.platform.tasks.spec import DependencyEdge

        # T1 (10 min) -> T2 (20 min) -> T4 (5 min) : total = 35 min
        # T1 -> T3 (5 min) -> T4 : total = 20 min
        t1 = TaskSpec(id="T-1", title="1", goal="g", max_runtime_minutes=10, priority=10)
        t2 = TaskSpec(id="T-2", title="2", goal="g", max_runtime_minutes=20, priority=20, requires_tasks=["T-1"])
        t3 = TaskSpec(id="T-3", title="3", goal="g", max_runtime_minutes=5, priority=30, requires_tasks=["T-1"])
        t4 = TaskSpec(id="T-4", title="4", goal="g", max_runtime_minutes=5, priority=100, requires_tasks=["T-2", "T-3"])

        all_tasks = [t1, t2, t3, t4]

        # 1. Critical path identifica o caminho mais longo (T-1 -> T-2 -> T-4)
        cp = compute_deterministic_critical_path(all_tasks)
        self.assertIn("T-1", cp)
        self.assertIn("T-2", cp)
        self.assertIn("T-4", cp)
        self.assertNotIn("T-3", cp)

        # 2. Priority Inheritance: T-4 (100) propaga prioridade para T-2, T-3 e T-1
        inherited = compute_inherited_priorities(all_tasks)
        self.assertEqual(inherited["T-4"], 100.0)
        self.assertEqual(inherited["T-2"], 100.0)
        self.assertEqual(inherited["T-3"], 100.0)
        self.assertEqual(inherited["T-1"], 100.0)

        # 3. Schedule next com all_tasks calcula e agenda T-1 primeiro
        sched = HAOSScheduler(max_concurrency=4)
        ready = [t1]
        assign = sched.schedule_next(ready, all_tasks=all_tasks)
        self.assertIsNotNone(assign)
        self.assertEqual(assign.task_id, "T-1")


class TestClassify(unittest.TestCase):
    def test_default_preserves_historical_shape(self):
        self.assertEqual(classify_execution([]),
                         (SHAPE_EPHEMERAL, LANE_HERMES, "ephemeral"))
        self.assertEqual(classify_execution(["git"]),
                         (SHAPE_WORKER_LANE, LANE_KILO, "ephemeral"))
        self.assertEqual(classify_execution([], "git_worktree"),
                         (SHAPE_WORKER_LANE, LANE_KILO, "ephemeral"))

    def test_reuse_shapes_on_hermes_lane(self):
        self.assertEqual(classify_execution([], reuse="persistent"),
                         (SHAPE_PERSISTENT, LANE_HERMES, "persistent"))
        self.assertEqual(classify_execution([], reuse="orchestrator"),
                         (SHAPE_SUB_ORCH, LANE_HERMES, "ephemeral"))

    def test_eight_execution_shapes(self):
        # Valida cobertura das 8 formas de execução declaradas
        self.assertEqual(classify_execution([], reuse="self")[0], "self")
        self.assertEqual(classify_execution([], reuse="posture_switch")[0], "posture_switch")
        self.assertEqual(classify_execution([], reuse="persistent")[0], "persistent_specialist")
        self.assertEqual(classify_execution([], reuse="none")[0], "ephemeral_worker")
        self.assertEqual(classify_execution([], reuse="orchestrator")[0], "sub_orchestrator")
        self.assertEqual(classify_execution(["git"])[0], "worker_lane")
        self.assertEqual(classify_execution([], reuse="capability_worker")[0], "capability_worker")
        self.assertEqual(classify_execution(["image-understanding"], workspace_type="scratch")[0], "capability_worker")
        self.assertEqual(classify_execution(["anp"], reuse="external_agent")[0], "external_agent")

    def test_git_dominates_reuse(self):
        for reuse in ("persistent", "orchestrator"):
            self.assertEqual(classify_execution(["git"], reuse=reuse),
                             (SHAPE_WORKER_LANE, LANE_KILO, "ephemeral"))

    def test_invalid_reuse_fails_fast(self):
        with self.assertRaises(InvalidReuseError):
            classify_execution([], reuse="quantum")

    def test_parity_with_lane_for_spec(self):
        # Mesma decisão de lane da fronteira real do dispatcher (contrato).
        for caps, ws in (([], None), (["git"], None), ([], "git_worktree")):
            shape, lane, _ = classify_execution(caps, ws)
            spec = {"required_capabilities": caps, "workspace_type": ws}
            self.assertEqual(lane, lane_for_spec(spec), f"caps={caps} ws={ws}")
            self.assertEqual(lane, LANE_KILO if "git" in (caps or []) or ws == "git_worktree" else LANE_HERMES)


class TestSpawnReuse(unittest.TestCase):
    def test_spawn_resolver_applies_reuse(self):
        r = SpawnResolver()
        a = r.resolve(TaskSpec(id="T-P", title="t", goal="g", reuse="persistent",
                              workspace_type="scratch"))
        self.assertEqual(a.execution_shape, SHAPE_PERSISTENT)
        self.assertEqual(a.agent_mode, "persistent")
        self.assertEqual(a.lane, LANE_HERMES)
        b = r.resolve(TaskSpec(id="T-O", title="t", goal="g", reuse="orchestrator",
                              workspace_type="scratch"))
        self.assertEqual(b.execution_shape, SHAPE_SUB_ORCH)
        with self.assertRaises(InvalidReuseError):
            r.resolve(TaskSpec(id="T-B", title="t", goal="g", reuse="bad",
                                workspace_type="scratch"))

    def test_spec_default_and_round_trip(self):
        self.assertEqual(TaskSpec(id="T-D", title="t", goal="g").reuse, "none")
        root = Path(tempfile.mkdtemp())
        old_kanban = os.environ.get("HERMES_KANBAN_HOME")
        old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban")
        os.environ["HERMES_HOME"] = str(root / "home")
        try:
            adapter = KanbanAdapter(root / "kanban.db")
            try:
                tid = adapter.save_task(
                    TaskSpec(id="T-RT", title="t", goal="g", reuse="orchestrator"),
                    status="READY",
                )
                persisted = adapter.get_task(tid)["spec"]
                self.assertEqual(persisted["reuse"], "orchestrator")
            finally:
                adapter.close()
        finally:
            if old_kanban is None:
                os.environ.pop("HERMES_KANBAN_HOME", None)
            else:
                os.environ["HERMES_KANBAN_HOME"] = old_kanban
            if old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
