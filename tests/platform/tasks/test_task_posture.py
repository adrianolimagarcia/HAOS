import tempfile
import unittest
from pathlib import Path

from hermes.platform.posture.resolver import PostureResolver, PostureSpec
from hermes.platform.tasks.spec import TaskSpec, AcceptanceCriterion, DependencyEdge
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.tasks.acceptance import AcceptanceEngine
from hermes.platform.tasks.failure_classifier import FailureClassifier
from hermes.platform.execution.runs import TaskRun

class TestTaskAndPosture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "kanban.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_posture_resolver(self):
        resolver = PostureResolver()
        arch = resolver.resolve("architect")
        self.assertEqual(arch.name, "Software Architect")
        self.assertEqual(arch.model_profile, "architecture-primary")

    def test_task_lifecycle_on_canonical_kanban(self):
        spec = TaskSpec(id="T-100", title="Test Task", goal="Verify task engine")
        adapter = KanbanAdapter(self.db)
        upstream_id = adapter.save_task(spec, status="READY")
        self.assertTrue(upstream_id.startswith("t_"))

        # Ready card + HAOS spec resolvable by either id.
        t = adapter.get_task("T-100")
        self.assertEqual(t["status"], "ready")
        self.assertEqual(t["spec"]["id"], "T-100")
        self.assertEqual(adapter.get_task(upstream_id)["id"], upstream_id)

        # Atomic claim: only the first worker wins.
        self.assertTrue(adapter.claim_task("T-100", "worker-1"))
        t = adapter.get_task("T-100")
        self.assertEqual(t["status"], "running")
        self.assertFalse(adapter.claim_task("T-100", "worker-2"))

        # Heartbeat on the live claim.
        self.assertTrue(adapter.heartbeat("T-100", "worker-1"))
        # Stranger worker cannot heartbeat another's claim
        self.assertFalse(adapter.heartbeat("T-100", "worker-stranger"))

        # Complete -> done + HAOS TaskResult/ended TaskRun snapshots.
        self.assertTrue(adapter.complete_task(
            "T-100", summary="green", evidence={"tests": {"passed": 5}},
            artifacts=["git://HEAD"], residual_risk=["nao-coberto: e2e"],
        ))
        t = adapter.get_task("T-100")
        self.assertEqual(t["status"], "done")
        result = adapter.get_result("T-100")
        self.assertEqual(result.summary, "green")
        self.assertEqual(result.evidence["tests"]["passed"], 5)
        self.assertIn("git://HEAD", result.artifacts)
        run = adapter.get_run("T-100")
        self.assertIsNotNone(run.run_id)
        self.assertEqual(run.status, "ended")
        self.assertEqual(run.exit_reason, "accepted")
        adapter.close()

    def test_spec_id_and_revision_idempotency(self):
        adapter = KanbanAdapter(self.db)
        spec = TaskSpec(id="T-101", title="Idem", goal="g", version=1)
        first = adapter.save_task(spec, status="READY")
        again = adapter.save_task(spec, status="READY")
        self.assertEqual(first, again)  # idempotency_key -> same canonical card
        # A new revision upserts the spec, same card.
        spec2 = TaskSpec(id="T-101", title="Idem", goal="g2", version=2)
        self.assertEqual(adapter.save_task(spec2, status="READY"), first)
        self.assertEqual(adapter.get_task("T-101")["spec"]["goal"], "g2")
        adapter.close()

    def test_spec_validation_fail_fast(self):
        # Validação de strategy inválida
        with self.assertRaises(ValueError):
            TaskSpec(id="T-INV-1", title="t", goal="g", strategy="invalid_strategy")

        # Validação de reuse inválido
        with self.assertRaises(ValueError):
            TaskSpec(id="T-INV-2", title="t", goal="g", reuse="invalid_reuse")

        # Validação de DependencyEdge.kind inválido
        with self.assertRaises(ValueError):
            DependencyEdge(source_task="T-1", target_task="T-2", kind="invalid_kind")

        # Valores válidos passam sem erro
        edge = DependencyEdge(source_task="T-1", target_task="T-2", kind="requires")
        self.assertEqual(edge.kind, "requires")
        spec = TaskSpec(id="T-VAL", title="t", goal="g", strategy="direct", reuse="persistent")
        self.assertEqual(spec.strategy, "direct")
        self.assertEqual(spec.reuse, "persistent")

    def test_execution_plan_persistence(self):
        from hermes.platform.tasks.execution_plan import ExecutionPlan
        adapter = KanbanAdapter(self.db)
        spec = TaskSpec(id="T-PLAN-1", title="Plan Test", goal="Testing plan persistence")
        upstream_id = adapter.save_task(spec, status="READY")

        # Inicialmente sem plano
        self.assertIsNone(adapter.load_execution_plan("T-PLAN-1"))
        self.assertIsNone(adapter.get_task("T-PLAN-1")["plan"])

        # Armazena plano
        plan = ExecutionPlan(
            task_id="T-PLAN-1",
            approach_summary="Step-by-step implementation",
            steps=["setup", "implement", "test"],
            delegations=[{"kind": "capability", "target": "vision"}],
            revision=1,
            generated_by="architect",
        )
        adapter.store_execution_plan("T-PLAN-1", plan)

        # Recupera plano por spec id e por upstream id
        loaded_by_spec = adapter.load_execution_plan("T-PLAN-1")
        self.assertIsNotNone(loaded_by_spec)
        self.assertEqual(loaded_by_spec.approach_summary, "Step-by-step implementation")
        self.assertEqual(loaded_by_spec.steps, ["setup", "implement", "test"])
        self.assertEqual(loaded_by_spec.delegations, [{"kind": "capability", "target": "vision"}])

        loaded_by_upstream = adapter.load_execution_plan(upstream_id)
        self.assertIsNotNone(loaded_by_upstream)
        self.assertEqual(loaded_by_upstream.approach_summary, loaded_by_spec.approach_summary)

        # Verifica integração no get_task
        task_info = adapter.get_task("T-PLAN-1")
        self.assertIsNotNone(task_info["plan"])
        self.assertEqual(task_info["plan"].approach_summary, "Step-by-step implementation")
        adapter.close()

    def test_unknown_task_raises(self):
        adapter = KanbanAdapter(self.db)
        with self.assertRaises(KeyError):
            adapter.claim_task("T-NOPE", "worker-1")
        with self.assertRaises(KeyError):
            adapter.heartbeat("T-NOPE", "worker-1")
        adapter.close()

    def test_heartbeat_unclaimed_or_stranger(self):
        adapter = KanbanAdapter(self.db)
        spec = TaskSpec(id="T-HB", title="HB Test", goal="heartbeat validation")
        adapter.save_task(spec, status="READY")

        # Unclaimed card (ready status) returns False
        self.assertFalse(adapter.heartbeat("T-HB", "worker-1"))

        # Claimed card returns True for owner, False for stranger
        self.assertTrue(adapter.claim_task("T-HB", "worker-owner"))
        self.assertTrue(adapter.heartbeat("T-HB", "worker-owner"))
        self.assertFalse(adapter.heartbeat("T-HB", "worker-stranger"))

        adapter.close()

    def test_requires_tasks_must_exist(self):
        adapter = KanbanAdapter(self.db)
        parent = TaskSpec(id="T-P", title="Parent", goal="p")
        pid = adapter.save_task(parent, status="READY")
        child = TaskSpec(id="T-C", title="Child", goal="c", requires_tasks=["T-P"])
        cid = adapter.save_task(child, status="READY")
        # parent not done -> child parked as todo by upstream
        self.assertEqual(adapter.get_task(cid)["status"], "todo")
        adapter.complete_task(pid, summary="done")
        with self.assertRaises(ValueError):
            adapter.save_task(TaskSpec(id="T-X", title="X", goal="x",
                                       requires_tasks=["T-GHOST"]), status="READY")
        adapter.close()

    def test_acceptance_and_failure(self):
        crit = AcceptanceCriterion(id="AC-1", description="Check structural", type="structural")
        res = AcceptanceEngine.validate_criterion(crit, {"structural_ok": True})
        self.assertTrue(res["passed"])

        cat = FailureClassifier.classify("HTTP 429 Too Many Requests")
        self.assertEqual(cat, FailureClassifier.CATEGORIES["PROVIDER_ERROR"])
        policy = FailureClassifier.retry_policy_for(cat)
        self.assertEqual(policy["action"], "provider_failover")
        self.assertFalse(policy["increments_task_retry"])

        cat_review = FailureClassifier.classify("Review rejected: changes requested")
        self.assertEqual(cat_review, FailureClassifier.CATEGORIES["REVIEW_REJECTION"])
        policy_review = FailureClassifier.retry_policy_for(cat_review)
        self.assertEqual(policy_review["action"], "rework")
        self.assertFalse(policy_review["increments_task_retry"])

        # Teste anti-hazard de heurísticas
        self.assertEqual(FailureClassifier.classify("tool timeout"), FailureClassifier.CATEGORIES["TOOL_TRANSIENT"])
        self.assertEqual(FailureClassifier.classify("token limit reached"), FailureClassifier.CATEGORIES["BUDGET_EXCEEDED"])
        self.assertEqual(FailureClassifier.classify("parent task not completed"), FailureClassifier.CATEGORIES["DEPENDENCY_MISSING"])
        self.assertEqual(FailureClassifier.classify("process crash after OOM"), FailureClassifier.CATEGORIES["WORKER_CRASH"])

        # Políticas das novas categorias
        dep_pol = FailureClassifier.retry_policy_for(FailureClassifier.CATEGORIES["DEPENDENCY_MISSING"])
        self.assertEqual(dep_pol["action"], "wait_for_dependency")
        self.assertFalse(dep_pol["increments_task_retry"])

        hum_pol = FailureClassifier.retry_policy_for(FailureClassifier.CATEGORIES["HUMAN_INPUT_REQUIRED"])
        self.assertEqual(hum_pol["action"], "wait_for_human")
        self.assertFalse(hum_pol["increments_task_retry"])

    def test_artifacts_and_events_persistence(self):
        adapter = KanbanAdapter(self.db)
        spec = TaskSpec(id="T-300", title="Artifacts Test", goal="Test artifacts and events")
        upstream_id = adapter.save_task(spec, status="READY")

        # Registra artefato
        adapter.record_artifact(
            upstream_id,
            artifact_id="A-001",
            artifact_type="source_patch",
            uri="git://repo/diff.patch",
            summary="ANP Protocol adapter diff",
            metadata={"lines_changed": 150}
        )

        artifacts = adapter.list_artifacts(upstream_id)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["artifact_id"], "A-001")
        self.assertEqual(artifacts[0]["type"], "source_patch")

        # Registra eventos de ciclo de vida
        adapter.record_run_event(upstream_id, "run.started", {"worker": "w-71"})
        adapter.record_run_event(upstream_id, "capability.invoked", {"capability": "lsp"})

        events = adapter.list_run_events(upstream_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "run.started")
        self.assertEqual(events[1]["event_type"], "capability.invoked")
        adapter.close()

    def test_claim_records_taskrun_snapshot(self):
        adapter = KanbanAdapter(self.db)
        spec = TaskSpec(id="T-200", title="Snap", goal="g", posture="implementer")
        upstream_id = adapter.save_task(spec, status="READY")
        run = TaskRun(task_id=upstream_id, worker_id="w-1", posture_id="implementer",
                      model_profile_id="coding-primary",
                      resolved_model="deepseek-v4:flash", lane="kilo")
        self.assertTrue(adapter.claim_task(upstream_id, "w-1", run=run))
        got = adapter.get_run(upstream_id)
        self.assertEqual(got.worker_id, "w-1")
        self.assertEqual(got.posture_id, "implementer")
        self.assertEqual(got.resolved_model, "deepseek-v4:flash")
        adapter.close()

if __name__ == "__main__":
    unittest.main()
