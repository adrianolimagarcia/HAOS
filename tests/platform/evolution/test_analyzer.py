import unittest

from hermes.platform.evolution.analyzer import OuroborosAnalyzer
from hermes.platform.evals.baselines import BaselineStore
from hermes.platform.observability.metrics import MetricsCollector
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event


class TestOuroborosFed(unittest.TestCase):
    """K7 — Ouroboros alimentado: proposals são calculadas de dados reais
    (baselines + metrics + event stream), nunca texto fixo. A proposta MUDA
    quando os dados mudam."""

    def test_baseline_delta_drives_proposal_and_changes_with_data(self):
        store = BaselineStore(":memory:")
        # model-a (referência) fixo; model-b pior primeiro, melhor depois.
        store.save("suite-adr", "model-a", {"pass_rate": 0.9, "avg_score": 8.0})
        store.save("suite-adr", "model-b", {"pass_rate": 0.7, "avg_score": 6.5})
        analyzer = OuroborosAnalyzer()

        regressed = analyzer.compare_baselines(store, "suite-adr", "model-b", "model-a")
        self.assertEqual(len(regressed), 1)
        self.assertEqual(regressed[0]["mode"], "PROPOSAL_ONLY")
        self.assertEqual(regressed[0]["evidence"]["verdict"], "regressed")
        self.assertIn("Δ-0.2000", regressed[0]["rationale"])  # números reais, não texto fixo
        # Rollback proposto: segurar model-a.
        self.assertEqual(regressed[0]["proposed_profile"], "model-a")

        # Agora model-b supera model-a -> a MESMA chamada produz proposta oposta.
        store.save("suite-adr", "model-b", {"pass_rate": 0.95, "avg_score": 8.6})
        improved = analyzer.compare_baselines(store, "suite-adr", "model-b", "model-a")
        self.assertEqual(len(improved), 1)
        self.assertEqual(improved[0]["evidence"]["verdict"], "improved")
        self.assertEqual(improved[0]["proposed_profile"], "model-b")
        self.assertNotEqual(improved[0]["rationale"], regressed[0]["rationale"])

    def test_no_data_no_opinion(self):
        store = BaselineStore(":memory:")
        analyzer = OuroborosAnalyzer()
        # Sem baseline de um dos lados, não há opinião (sem texto fixo).
        self.assertEqual(analyzer.compare_baselines(store, "suite-x", "a", "b"), [])

    def test_metrics_failure_rate_and_cost_budgets(self):
        analyzer = OuroborosAnalyzer()
        m = MetricsCollector()
        m.increment("run.failed", 3)
        m.increment("run.completed", 2)
        m.record_cost(2.5)

        proposals = analyzer.analyze_metrics(m.summary(), max_cost_usd=1.0)
        self.assertEqual(len(proposals), 2)  # taxa de falha + custo
        self.assertTrue(all(p["mode"] == "PROPOSAL_ONLY" for p in proposals))
        self.assertIn("60.0%", proposals[0]["rationale"])  # 3/5 -> 60%
        self.assertIn("2.5000", proposals[1]["rationale"])

        # Dentro do orçamento e sem falhas -> nada.
        m2 = MetricsCollector()
        m2.increment("run.completed", 5)
        m2.record_cost(0.1)
        self.assertEqual(analyzer.analyze_metrics(m2.summary(), max_cost_usd=1.0), [])

    def test_event_store_failures_drive_proposal(self):
        analyzer = OuroborosAnalyzer()
        store = EventStore(":memory:")
        store.append(Event(name="task.completed", payload={"task_id": "T-1"}, trace_id="tr-1"))
        store.append(Event(name="run.failed", payload={"task_id": "T-2", "reason": "timeout"}, trace_id="tr-1"))
        store.append(Event(name="eval.performance", payload={"suite": "adr", "score": 0.6}, trace_id="tr-1"))

        proposals = analyzer.analyze_execution_history(event_store=store)
        names = {p["target"] for p in proposals}
        self.assertIn("observability:events", names)  # run.failed detectado
        self.assertIn("observability:evals", names)   # score 0.6 < 1.0
        self.assertTrue(all(p["mode"] == "PROPOSAL_ONLY" for p in proposals))

    def test_facade_combines_real_sources(self):
        store = BaselineStore(":memory:")
        store.save("suite-adr", "model-a", {"pass_rate": 0.9})
        store.save("suite-adr", "model-b", {"pass_rate": 0.6})
        m = MetricsCollector()
        m.increment("run.failed", 1)
        m.record_cost(0.4)

        analyzer = OuroborosAnalyzer()
        proposals = analyzer.analyze_execution_history(
            baseline_store=store, suite_id="suite-adr",
            candidate_label="model-b", reference_label="model-a",
            metrics=m.summary(), max_cost_usd=10.0,
        )
        targets = {p["target"] for p in proposals}
        self.assertIn("suite:suite-adr", targets)          # baseline delta
        self.assertIn("observability:runs", targets)      # falha nos metrics


if __name__ == "__main__":
    unittest.main()
