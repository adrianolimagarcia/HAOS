"""Testes de regressão cobrindo os achados A02 a A11 e otimização do EventStore da auditoria técnica HAOS."""

import os
import sys
import time
import tempfile
import subprocess
from pathlib import Path
import unittest

from hermes.platform.observability.events import Event
from hermes.platform.observability.event_store import EventStore
from hermes.platform.workspaces.merge_queue import MergeQueue, MergeStatus, MergeCandidate
from hermes.platform.webui.controlplane import ControlPlaneService
from hermes.platform.models.client import ExactModelClient, ModelRouteExhaustedException
from hermes.platform.models.provider_router import ExactModelRouter, subscribe_route_exhausted, unsubscribe_route_exhausted
from hermes.platform.models.profiles import ModelProfile, ModelIdentity, ProviderRoute
from hermes.platform.context.policies.policy import ContextPolicy, SectionBudgetRatio
from hermes.platform.context.primitives.item import ContextItem, TrustLevel
from hermes.platform.context.budget.budgeter import ContextBudgeter
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.execution.dispatcher import HAOSDispatcher
from hermes.platform.execution.backpressure import ConcurrencyGuard
from hermes_cli import kanban_db_workspace as kbw


class TestAuditRegressions(unittest.TestCase):

    def test_a02_automerge_imports_sys(self):
        """A02: AutoMergeGate pode ser importado sem NameError 'sys'."""
        from hermes.platform.workspaces.automerge import AutoMergeGate
        self.assertIn("HERMES_PYTHON", AutoMergeGate.DEFAULT_CONTRACTS_CMD)
        self.assertIn(sys.executable, AutoMergeGate.DEFAULT_CONTRACTS_CMD)

    def test_a03_concurrency_guard_released_on_workspace_failure(self):
        """A03: Liberação de permissão de concorrência quando resolução de workspace falha."""
        guard = ConcurrencyGuard(max_global_concurrency=1)
        adapter = KanbanAdapter(board="test-board")
        spec = TaskSpec(id="T-WORKSPACE-FAIL", title="Task fail", goal="test", workspace_type="scratch")
        tid = adapter.save_task(spec, status="READY")

        dispatcher = HAOSDispatcher(
            adapter,
            board="test-board",
            concurrency_guard=guard,
        )

        original_resolve = kbw.resolve_workspace

        def fail_resolve(*args, **kwargs):
            raise OSError("Injected disk failure for workspace")

        kbw.resolve_workspace = fail_resolve
        try:
            dispatcher.claim_tick(max_spawn=1)
        finally:
            kbw.resolve_workspace = original_resolve

        # Guard deve estar liberado (active_workers_count == 0)
        self.assertEqual(guard.active_workers_count, 0)

    def test_a04_failed_evidence_rejected_in_complete_task(self):
        """A04: Evidência com testes falhos rejeita a tarefa em vez de auto-aprovar."""
        adapter = KanbanAdapter(board="test-board")
        spec = TaskSpec(id="T-FAIL-EVID", title="Task with test failure", goal="test")
        tid = adapter.save_task(spec, status="READY")
        conn = adapter._connect()
        import hermes_cli.kanban_db as kb
        kb.claim_task(conn, tid, claimer="w1")

        # Completar tarefa com evidência de testes falhos
        success = adapter.complete_task(
            tid,
            summary="Attempted execution",
            evidence={"tests": {"passed": 0, "failed": 2}},
        )
        self.assertFalse(success)
        result = adapter.get_result(tid)
        self.assertIsNotNone(result)
        self.assertEqual(result.reviewer_verdict, "rejected")
        self.assertEqual(result.acceptance[0]["status"], "failed")

    def test_a05_merge_requires_validator_when_mandatory(self):
        """A05: Fila de merge rejeita candidato se require_validator=True e validator_fn=None."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
            (repo / "f.txt").write_text("initial")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

            subprocess.run(["git", "checkout", "-b", "feat"], cwd=repo, check=True)
            (repo / "f.txt").write_text("modified")
            subprocess.run(["git", "commit", "-am", "mod"], cwd=repo, check=True)
            subprocess.run(["git", "checkout", "main"], cwd=repo, check=True)

            mq = MergeQueue(repo_root=repo, target_branch="main", require_validator=True)
            mq.enqueue(task_id="T1", branch="feat", test_report_hash="fake-hash")

            candidate = mq.process_next()
            self.assertEqual(candidate.status, MergeStatus.REJECTED)
            self.assertIn("validator", candidate.rejection_reason.lower())

    def test_a06_intervention_survives_restart(self):
        """A06: Intervenção registrada sobrevive ao restart do serviço ControlPlane."""
        store = EventStore(db_path=":memory:")
        cp1 = ControlPlaneService(event_store=store)
        cp1.record_intervention("worker-01", "pause", "Operator pause")

        self.assertEqual(cp1.get_pending_intervention("worker-01"), "pause")

        # Novo serviço ControlPlane inicializado com o mesmo store
        cp2 = ControlPlaneService(event_store=store)
        self.assertEqual(cp2.get_pending_intervention("worker-01"), "pause")

    def test_a07_mission_graph_isolation(self):
        """A07: O grafo de equipe filtra estritamente eventos da missão alvo."""
        store = EventStore(db_path=":memory:")
        cp = ControlPlaneService(event_store=store)

        # Evento da missão A
        store.append(Event(name="haos.task.spawned", payload={"mission_id": "mission-A", "task_id": "T-A", "assignee": "worker-A"}))
        # Evento da missão B
        store.append(Event(name="haos.task.spawned", payload={"mission_id": "mission-B", "task_id": "T-B", "assignee": "worker-B"}))

        snapshot_a = cp.get_team_graph_snapshot(mission_id="mission-A")
        sub_orch = snapshot_a["children"][0]
        worker_ids = [w["node_id"] for w in sub_orch["children"]]

        self.assertIn("worker-A", worker_ids)
        self.assertNotIn("worker-B", worker_ids)

    def test_a08_empty_store_has_no_invented_usage(self):
        """A08: Observabilidade não inventa métricas ou nós quando loja está vazia."""
        store = EventStore(db_path=":memory:")
        cp = ControlPlaneService(event_store=store)

        overview = cp.get_overview()
        self.assertEqual(overview.total_tokens, 0)
        self.assertEqual(overview.total_cost_usd, 0.0)
        self.assertEqual(overview.active_workers, 0)

        snapshot = cp.get_team_graph_snapshot(mission_id="mission-none")
        sub_orch = snapshot["children"][0]
        self.assertEqual(len(sub_orch["children"]), 0)

    def test_a09_client_emits_route_exhausted(self):
        """A09: ExactModelClient emite evento route_exhausted quando rotas falham."""
        events_emitted = []

        def listener(payload):
            events_emitted.append(payload)

        subscribe_route_exhausted(listener)
        try:
            profile = ModelProfile(
                id="test-profile",
                model_identity=ModelIdentity(family="test", variant="v1"),
                routes=[
                    ProviderRoute(provider_id="fake_prov", provider_model_id="m1", priority=1),
                ],
            )

            def failing_transport(*args, **kwargs):
                return 503, {"error": "service unavailable"}

            client = ExactModelClient(transport_fn=failing_transport)
            with self.assertRaises(ModelRouteExhaustedException):
                client.complete(profile, [{"role": "user", "content": "hi"}])

            self.assertEqual(len(events_emitted), 1)
            self.assertEqual(events_emitted[0]["model_family"], "test")
            self.assertIn("fake_prov", events_emitted[0]["exhausted_providers"])
        finally:
            unsubscribe_route_exhausted(listener)

    def test_a10_minimum_context_trust_is_enforced(self):
        """A10: Limiar mínimo de confiança em ContextPolicy bloqueia itens inferiores."""
        policy = ContextPolicy(
            posture_name="strict",
            min_trust_level=TrustLevel.TASK_SPEC,
            allow_external_untrusted=True,
        )

        untrusted_item = ContextItem(
            id="item-untrusted",
            item_type="task",
            source_uri="web://external",
            title="External Data",
            content="Some untrusted web content",
            trust=TrustLevel.EXTERNAL_UNTRUSTED,
        )

        allowed, reason = policy.is_item_allowed(untrusted_item)
        self.assertFalse(allowed)
        self.assertIn("lower than min_trust_level", reason)

    def test_a11_context_budget_rejects_oversized_protected_section(self):
        """A11: Seção protegida maior que o orçamento estoura com erro explícito."""
        policy = ContextPolicy(
            posture_name="coder",
            max_tokens=50,
        )
        budgeter = ContextBudgeter(policy=policy, total_budget=50)

        huge_task_item = ContextItem(
            id="task-huge",
            item_type="task",
            source_uri="task://huge",
            title="Task Spec",
            content="x" * 2000,  # ~500 tokens, excede orçamento de 50
            trust=TrustLevel.TASK_SPEC,
        )

        candidates = {
            "task": [huge_task_item],
        }

        with self.assertRaises(ValueError) as ctx:
            budgeter.fit_package(task_id="T-1", task_revision=1, candidates_by_section=candidates)

        self.assertIn("exceeds total token budget", str(ctx.exception))

    def test_event_store_limit_optimization(self):
        """Otimização EventStore: LIMIT aplicado via SQL direto."""
        store = EventStore(db_path=":memory:")
        for i in range(10):
            store.append(Event(name=f"ev.{i}", payload={"idx": i}))

        last_3 = store.get_all(limit=3)
        self.assertEqual(len(last_3), 3)
        self.assertEqual([e.payload["idx"] for e in last_3], [7, 8, 9])

        after_seq = store.events_after(seq=2, limit=2)
        self.assertEqual(len(after_seq), 2)
        self.assertEqual([e.payload["idx"] for e in after_seq], [8, 9])


if __name__ == "__main__":
    unittest.main()
