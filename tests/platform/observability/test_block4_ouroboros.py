import os
import tempfile
import unittest
from pathlib import Path

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.observability.sink import EventStoreSink
from hermes.platform.observability.replay import EventReplayer, ReplayError
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.evolution.analyzer import OuroborosAnalyzer
from hermes.platform.evolution.ledger import EvolutionLedger


class TestBlock4ObservabilityAndOuroboros(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.events_db = self.tmp_path / "events.db"
        self.kanban_db = self.tmp_path / "kanban.db"
        self.store = EventStore(self.events_db)
        self.sink = EventStoreSink(self.store)
        self.adapter = KanbanAdapter(self.kanban_db, event_sink=self.sink)

    def tearDown(self):
        self.adapter.close()
        self._tmp.cleanup()

    def test_kanban_adapter_projects_run_events_to_event_store(self):
        # 1. Cria tarefa e registra eventos
        t_id = self.adapter.save_task(TaskSpec(id="T-EV-1", title="Obs Task", goal="Test projection", workspace_type="scratch"))
        self.adapter.record_run_event(t_id, "step_started", payload={"step": 1}, run_uid="run-1")
        self.adapter.record_run_event(t_id, "step_finished", payload={"step": 1, "status": "ok"}, run_uid="run-1")

        # Verifica persistência local aditiva
        local_events = self.adapter.list_run_events(t_id)
        self.assertEqual(len(local_events), 2)

        # Verifica projeção no EventStore
        es_events = self.store.get_by_correlation_id(t_id)
        self.assertEqual(len(es_events), 2)
        self.assertEqual(es_events[0].name, "task.run.step_started")
        self.assertEqual(es_events[1].name, "task.run.step_finished")
        self.assertEqual(es_events[0].payload["step"], 1)

    def test_kanban_adapter_records_failure_and_reviewed_events(self):
        t_id = self.adapter.save_task(TaskSpec(id="T-EV-2", title="Fail Task", goal="Test failure", workspace_type="scratch"))
        
        # Simula falha categorizada
        self.adapter.record_task_failure(t_id, "Rate limit exceeded (HTTP 429)", outcome="provider_rate_limit")
        
        # Simula review verdict
        self.adapter.record_review_verdict(t_id, "approved", approver="human-lead", rationale="LGTM")

        # Verifica eventos no EventStore
        es_events = self.store.get_by_correlation_id(t_id)
        names = [e.name for e in es_events]
        self.assertIn("task.run.failure", names)
        self.assertIn("task.run.reviewed", names)

        fail_event = next(e for e in es_events if e.name == "task.run.failure")
        self.assertEqual(fail_event.payload["failure_category"], "provider_error")
        self.assertEqual(fail_event.payload["retry_action"]["action"], "provider_failover")
        self.assertFalse(fail_event.payload["retry_action"]["increments_task_retry"])

    def test_replay_fold_reconstructs_task_state(self):
        # Emite uma sequência de eventos com o mesmo trace_id
        trace_id = "trace-task-100"
        self.store.append(Event(name="task.created", payload={"title": "Init"}, trace_id=trace_id))
        self.store.append(Event(name="task.step", payload={"step": "code", "added": 10}, trace_id=trace_id))
        self.store.append(Event(name="task.step", payload={"step": "test", "added": 5}, trace_id=trace_id))
        self.store.append(Event(name="task.completed", payload={"status": "done"}, trace_id=trace_id))

        replayer = EventReplayer(self.store)

        def projector(state, event):
            if event.name == "task.created":
                state["status"] = "created"
            elif event.name == "task.step":
                state["loc"] = state.get("loc", 0) + event.payload.get("added", 0)
            elif event.name == "task.completed":
                state["status"] = event.payload["status"]
            return state

        result = replayer.replay_fold(trace_id, projector=projector, initial={"loc": 0})
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["loc"], 15)

    def test_ouroboros_analyzer_generates_proposals_from_structured_failures(self):
        # Grava eventos de falha categorizada
        self.store.append(Event(
            name="task.run.failure",
            payload={
                "task_id": "T-1",
                "failure_category": "acceptance_failure",
                "model": "deepseek-coder",
                "error": "Failed acceptance criterion AC-1",
            }
        ))
        self.store.append(Event(
            name="task.run.failure",
            payload={
                "task_id": "T-2",
                "failure_category": "review_rejection",
                "model": "deepseek-coder",
                "error": "Human reviewer rejected patch",
            }
        ))

        analyzer = OuroborosAnalyzer()
        proposals = analyzer.analyze_execution_history(event_store=self.store)

        # Deve gerar proposta de elevação de fidelidade/postura para deepseek-coder
        self.assertTrue(len(proposals) > 0)
        p = next((prop for prop in proposals if "deepseek-coder" in prop["target"]), None)
        self.assertIsNotNone(p)
        self.assertEqual(p["mode"], "PROPOSAL_ONLY")
        self.assertEqual(p["proposed_profile"], "deepseek-coder:high_fidelity")
        self.assertIn("falha(s) de aceitação/revisão", p["rationale"])

        # Testa integração com o EvolutionLedger
        ledger = EvolutionLedger(self.store)
        p_id = ledger.submit(p)
        self.assertIsNotNone(p_id)

        pending = ledger.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["proposal_id"], p_id)

        # Aprova a proposta
        ledger.decide(p_id, "approved", approver="tech-lead", rationale="Approved based on empirical acceptance failure rate")
        self.assertEqual(len(ledger.pending()), 0)


if __name__ == "__main__":
    unittest.main()
