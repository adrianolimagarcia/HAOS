import unittest

from hermes.platform.evolution.analyzer import OuroborosAnalyzer
from hermes.platform.evolution.ledger import EvolutionLedger, LedgerError
from hermes.platform.evals.baselines import BaselineStore
from hermes.platform.observability.event_store import EventStore
from hermes.platform.ui.stats import DashboardStats

_PROPOSAL_SUBMITTED = "evolution.proposal.submitted"
_PROPOSAL_DECIDED = "evolution.proposal.decided"


def _analyzer_proposal():
    """Proposta real do analyzer (baseline regredida) para os testes."""
    store = BaselineStore(":memory:")
    store.save("suite-adr", "model-a", {"pass_rate": 0.9, "avg_score": 8.0})
    store.save("suite-adr", "model-b", {"pass_rate": 0.6, "avg_score": 5.0})
    analyzer = OuroborosAnalyzer()
    return analyzer.compare_baselines(store, "suite-adr", "model-b", "model-a")[0]


class TestEvolutionLedger(unittest.TestCase):
    """Delta 44 — proposals do analyzer (K7) viram decisões PERSISTIDAS no
    EventStore (append-only), nunca aplicação automática (shadow mode)."""

    def setUp(self):
        self.store = EventStore(":memory:")
        self.ledger = EvolutionLedger(self.store)

    def test_submit_persists_as_event_and_is_idempotent(self):
        proposal = _analyzer_proposal()
        pid = self.ledger.submit(proposal)
        self.assertTrue(pid)
        # Mesma proposta submetida de novo -> mesmo id, sem duplicar no stream.
        self.assertEqual(self.ledger.submit(proposal), pid)
        self.assertEqual(len(self.ledger.pending()), 1)
        names = {e["name"] for e in self.ledger.history()}
        self.assertEqual(names, {_PROPOSAL_SUBMITTED})

    def test_pending_then_decide_clears_pending(self):
        pid = self.ledger.submit(_analyzer_proposal())
        self.assertEqual(len(self.ledger.pending()), 1)

        self.ledger.decide(pid, "approved", approver="lead", rationale="dados confirmam")
        self.assertEqual(self.ledger.pending(), [])
        decided = [e for e in self.ledger.history() if e["name"] == _PROPOSAL_DECIDED]
        self.assertEqual(len(decided), 1)
        payload = decided[0]["payload"]
        self.assertEqual(payload["proposal_id"], pid)
        self.assertEqual(payload["verdict"], "approved")
        self.assertEqual(payload["approver"], "lead")
        # A decisão carrega a evidência medida (nunca texto fixo).
        self.assertEqual(payload["evidence"]["verdict"], "regressed")

    def test_reject_is_recorded_and_pending_keeps_other(self):
        p1 = self.ledger.submit(_analyzer_proposal())
        # Proposta distinta (mesma suite, outro candidato) -> id diferente.
        store = BaselineStore(":memory:")
        store.save("suite-adr", "model-a", {"pass_rate": 0.9})
        store.save("suite-adr", "model-c", {"pass_rate": 0.55})
        other = OuroborosAnalyzer().compare_baselines(store, "suite-adr", "model-c", "model-a")[0]
        p2 = self.ledger.submit(other)
        self.assertNotEqual(p1, p2)

        self.ledger.decide(p1, "rejected", approver="ops", rationale="falso positivo")
        # p2 continua pendente (decisão é por proposta, não global).
        pending_ids = {p["proposal_id"] for p in self.ledger.pending()}
        self.assertEqual(pending_ids, {p2})

    def test_decide_fails_closed(self):
        proposal = _analyzer_proposal()
        # Decidir antes de submeter -> erro (não há decisão fantasma).
        with self.assertRaises(LedgerError):
            self.ledger.decide("nope", "approved", approver="lead")
        # Veredito inválido.
        pid = self.ledger.submit(proposal)
        with self.assertRaises(LedgerError):
            self.ledger.decide(pid, "maybe", approver="lead")
        # Decidir duas vezes -> erro (não sobrescreve).
        self.ledger.decide(pid, "approved", approver="lead")
        with self.assertRaises(LedgerError):
            self.ledger.decide(pid, "rejected", approver="lead")
        # Approver vazio -> erro.
        pid2 = self.ledger.submit(proposal)
        with self.assertRaises(LedgerError):
            self.ledger.decide(pid2, "approved", approver="")

    def test_empty_store_no_pending(self):
        self.assertEqual(self.ledger.pending(), [])
        self.assertEqual(self.ledger.history(), [])


class TestEvolutionPendingDerived(unittest.TestCase):
    """Delta 44 — DashboardStats expõe a fila de decisão (control plane)."""

    def test_dashboard_exposes_pending_proposals(self):
        store = EventStore(":memory:")
        ledger = EvolutionLedger(store)
        pid = ledger.submit(_analyzer_proposal())

        stats = DashboardStats(event_store=store)
        pending = stats.evolution_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["proposal_id"], pid)

        ledger.decide(pid, "approved", approver="lead")
        self.assertEqual(stats.evolution_pending(), [])

    def test_no_event_store_fails_safe_empty(self):
        stats = DashboardStats()  # sem kanban e sem store
        self.assertEqual(stats.evolution_pending(), [])


if __name__ == "__main__":
    unittest.main()
