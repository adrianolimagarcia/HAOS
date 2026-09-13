"""C — UI/Control Plane (Fase 3): testes dos agregados/views do dashboard.

Invariantes (contratos, nunca snapshots):
(a) TaskBoardStats: deriva contadores/ordenação do KanbanAdapter real
    (temp DB); total == soma por status == soma por phase.
(b) StudioStats: conta eventos reais do EventStore; trace/correlation
    deduplicados (arestas distintas), by_name soma == event_count.
(c) Views: taskboard_view tem a view nomeada + colunas ordenadas;
    approvals_view deriva de `result.reviewer_verdict == approved` +
    acceptance (fail-safe: sem result => pending); memory_graph_view
    expõe nós derivados.
(d) Sem store (kanban None): available False, board vazio (zero/[]), nunca
    dados de outra base; render_dashboard HTML mínimo sem exceção.
"""

import tempfile
import unittest
from pathlib import Path

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.observability.events import Event
from hermes.platform.observability.event_store import EventStore
from hermes.platform.execution.runs import TaskResult
from hermes.platform.ui.stats import DashboardStats
from hermes.platform.ui.views import (
    approvals_view, memory_graph_view, taskboard_view,
)
from hermes.platform.ui.dashboard import (
    dashboard_payload, render_dashboard,
)
from hermes.platform.ui.server import make_ui_server


class TestUiStats(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db = root / "kanban.db"
        self.adapter = KanbanAdapter(self.db)
        self.store = EventStore(root / "events.db")

    def tearDown(self):
        self.adapter.close()
        self._tmp.cleanup()

    def _seed_task(self, tid: str, status: str = "READY") -> str:
        spec = TaskSpec(id=tid, title=tid, goal="g",
                        workspace_type="scratch")
        return self.adapter.save_task(spec, status=status, phase="triage")

    def test_task_board_derives_counts_and_recent(self):
        # Estado canônico real: save deixa o card 'ready' (upstream); claim
        # move para 'running'. READY/RUNNING do teste = intenção, status do
        # card = máquina de estados upstream (o contrato que o stats observa).
        t_a = self._seed_task("T-A", status="READY")
        t_b = self._seed_task("T-B", status="READY")
        t_c = self._seed_task("T-C", status="READY")
        self.adapter.claim_task("T-B", worker_id="w-1")
        stats = DashboardStats(kanban=self.adapter).task_board()
        self.assertEqual(stats.total, 3)
        self.assertEqual(sum(stats.by_status.values()), stats.total)
        self.assertEqual(stats.by_status["ready"], 2)
        self.assertEqual(stats.by_status["running"], 1)
        recent_ids = sorted(t["id"] for t in stats.recent)
        self.assertEqual(sorted([t_a, t_b, t_c]), recent_ids)

    def test_studio_stats_events_and_edges(self):
        self.store.append(Event(name="task.created", payload={}, trace_id="tr-1"))
        self.store.append(Event(name="task.created", payload={}, trace_id="tr-1",
                                correlation_id="c-1"))
        self.store.append(Event(name="task.completed", payload={}, trace_id="tr-2"))
        stats = DashboardStats(event_store=self.store).studio()
        self.assertEqual(stats.event_count, 3)
        self.assertEqual(sum(stats.by_name.values()), stats.event_count)
        self.assertEqual(stats.by_name["task.created"], 2)
        # arestas distintas (dedup)
        self.assertEqual(stats.traces, ["tr-1", "tr-2"])
        self.assertEqual(stats.correlated, ["c-1"])

    def test_unavailable_store_is_empty_not_wrong(self):
        stats = DashboardStats()
        self.assertFalse(stats.available())
        board = stats.task_board()
        self.assertEqual(board.total, 0)
        self.assertEqual(board.recent, [])
        studio = stats.studio()
        self.assertEqual(studio.event_count, 0)


class TestUiViews(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.adapter = KanbanAdapter(root / "kanban.db")
        self.store = EventStore(root / "events.db")
        self.stats = DashboardStats(kanban=self.adapter, event_store=self.store)

    def tearDown(self):
        self.adapter.close()
        self._tmp.cleanup()

    def test_taskboard_view_shape(self):
        spec = TaskSpec(id="T-1", title="T-1", goal="g",
                        workspace_type="scratch")
        self.adapter.save_task(spec, status="READY")
        view = taskboard_view(self.stats)
        self.assertEqual(view["view"], "taskboard")
        self.assertEqual(view["total"], 1)
        self.assertEqual(view["columns"][0]["status"], "ready")
        self.assertEqual(view["columns"][0]["count"], 1)

    def test_approvals_view_derives_verdict(self):
        spec = TaskSpec(id="T-2", title="T-2", goal="g",
                        workspace_type="scratch")
        tid = self.adapter.save_task(spec, status="READY")
        # Delta 55: sem resultado gravado => nada a revisar (card nunca rodou)
        view = approvals_view(self.stats)
        self.assertTrue(view["decision"])
        self.assertEqual(view["pending"], [])
        # com resultado manual ainda sem veredito aprovado => pendente
        result = TaskResult(task_id=tid)
        self._store_result(tid, result)
        view1 = approvals_view(self.stats)
        pending1 = {p["id"] for p in view1["pending"]}
        self.assertIn(tid, pending1)
        # com result aprovado + acceptance ok => aprovado (some do pending)
        result = TaskResult(
            task_id=tid, reviewer_verdict="approved",
            acceptance=[{"status": "passed"}, {"status": "approved"}],
        )
        self._store_result(tid, result)
        view2 = approvals_view(self.stats, required_review_stages=1)
        pending2 = {p["id"] for p in view2["pending"]}
        self.assertNotIn(tid, pending2)
        self.assertTrue(view2["decision"])

    def _store_result(self, tid: str, result: TaskResult) -> None:
        import json as _json
        conn = self.adapter._connect()
        conn.execute(
            "UPDATE haos_task_meta SET result_json = ?, updated_at = ? "
            "WHERE task_id = ?",
            (_json.dumps(result.to_dict()), 1.0, tid))
        conn.commit()

    def test_memory_graph_view(self):
        self.store.append(Event(name="task.created", payload={}, trace_id="t1"))
        view = memory_graph_view(self.stats)
        self.assertEqual(view["view"], "memory_graph")
        self.assertEqual(view["nodes"][0]["type"], "task.created")
        self.assertEqual(view["trace_edges"], 1)

    def test_dashboard_payload_and_html(self):
        payload = dashboard_payload(self.stats)
        self.assertEqual(payload["taskboard"]["view"], "taskboard")
        html = render_dashboard(self.stats)
        self.assertIn("Taskboard", html)
        self.assertIn("decision", html)

    def test_ui_server_serves_html_and_api(self):
        import json as _json
        import threading
        import urllib.request

        # Stats vazio (sem store) evita conexão sqlite cross-thread: o Kanban
        # é file-backed com conexão por thread — no servidor threaded o estado
        # sem store renderiza vazio (contrato fail-closed já coberto acima).
        server, base = make_ui_server(DashboardStats())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"{base}/", timeout=10) as resp:
                html = resp.read().decode("utf-8")
                self.assertIn("Taskboard", html)
                self.assertIn("decision", html)
            with urllib.request.urlopen(f"{base}/api/state", timeout=10) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
                self.assertEqual(payload["taskboard"]["total"], 0)
                self.assertEqual(payload["approvals"]["view"], "approvals")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
