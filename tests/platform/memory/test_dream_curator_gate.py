"""Testes para o gate de promoção Dream/Curator e MemoryRouter.

Valida:
- Bloqueio explícito de HYPOTHESIS, FACT_UNVERIFIED e REFUTED.
- Aceitação estrita apenas de FACT_VERIFIED com referências de evidência (evidence_refs).
- Comportamento no MemoryRouter, MemoryCandidate (is_high_confidence, validate_candidate),
  _CanonicalLessonWriter e MemoryReconciler.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hermes.platform.context.memory.candidate import (
    MemoryCandidate,
    validate_candidate,
)
from hermes.platform.context.memory.classifier import (
    AssertionClassification,
    AssertionType,
    classify_assertion,
)
from hermes.platform.context.memory.router import (
    MemoryRouter,
    STAGE_REASON_BLOCKED_ASSERTION_TYPE,
    STAGE_REASON_MISSING_EVIDENCE,
)
from hermes.platform.context.memory.staging import MemoryStagingStore, candidate_key
from hermes.platform.memory.dream import _CanonicalLessonWriter
from hermes.platform.memory.reconciler import MemoryReconciler


class TestDreamCuratorGate(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.path = Path(self.temp_dir.name)
        self.staging_store = MemoryStagingStore(self.path / "staging")
        self.router = MemoryRouter(staging_store=self.staging_store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_router_blocks_hypothesis_from_promotion(self):
        """HYPOTHESIS não pode ser promovido, mesmo com alta confiança, e vai para staging."""
        c = MemoryCandidate(
            fact="Talvez o usuário use Neovim no terminal",
            source_uri="session://s1",
            confidence=0.95,
            classification=AssertionClassification(
                assertion_type=AssertionType.HYPOTHESIS,
                confidence=0.9,
                evidence_refs=["session://s1"],
            ),
        )
        provider = {}
        res = self.router.process_candidate(c, provider)
        self.assertFalse(res)
        self.assertEqual(c.status, "pending")
        staged = self.staging_store.get(candidate_key(c.fact))
        self.assertIsNotNone(staged)
        assert staged is not None
        self.assertEqual(staged["gate"], STAGE_REASON_BLOCKED_ASSERTION_TYPE)

    def test_router_blocks_unverified_fact_from_promotion(self):
        """FACT_UNVERIFIED é bloqueado e permanece em staging."""
        c = MemoryCandidate(
            fact="O usuário usa PostgreSQL em produção",
            source_uri="session://s1",
            confidence=0.95,
            classification=AssertionClassification(
                assertion_type=AssertionType.FACT_UNVERIFIED,
                confidence=0.6,
                evidence_refs=["session://s1"],
            ),
        )
        provider = {}
        res = self.router.process_candidate(c, provider)
        self.assertFalse(res)
        self.assertEqual(c.status, "pending")
        staged = self.staging_store.get(candidate_key(c.fact))
        self.assertIsNotNone(staged)
        assert staged is not None
        self.assertEqual(staged["gate"], STAGE_REASON_BLOCKED_ASSERTION_TYPE)

    def test_router_blocks_refuted_from_promotion(self):
        """REFUTED é bloqueado da promoção e fica em staging com motivo auditável."""
        c = MemoryCandidate(
            fact="Ficou comprovado que o banco não era MongoDB",
            source_uri="session://s1",
            confidence=0.95,
            classification=AssertionClassification(
                assertion_type=AssertionType.REFUTED,
                confidence=0.95,
                evidence_refs=["session://s1"],
            ),
        )
        provider = {}
        res = self.router.process_candidate(c, provider)
        self.assertFalse(res)
        self.assertEqual(c.status, "pending")
        staged = self.staging_store.get(candidate_key(c.fact))
        self.assertIsNotNone(staged)
        assert staged is not None
        self.assertEqual(staged["gate"], STAGE_REASON_BLOCKED_ASSERTION_TYPE)

    def test_router_blocks_fact_verified_without_evidence(self):
        """FACT_VERIFIED sem evidências válidas é bloqueado com motivo missing_evidence_refs."""
        c = MemoryCandidate(
            fact="Segundo o usuário, o fuso horário é UTC-3",
            source_uri="session://s1",
            confidence=0.95,
            classification=AssertionClassification(
                assertion_type=AssertionType.FACT_VERIFIED,
                confidence=0.85,
                evidence_refs=[],
            ),
        )
        provider = {}
        res = self.router.process_candidate(c, provider)
        self.assertFalse(res)
        self.assertEqual(c.status, "pending")
        staged = self.staging_store.get(candidate_key(c.fact))
        self.assertIsNotNone(staged)
        assert staged is not None
        self.assertEqual(staged["gate"], STAGE_REASON_MISSING_EVIDENCE)

    def test_router_promotes_fact_verified_with_valid_evidence(self):
        """FACT_VERIFIED com evidence_refs válido e alta confiança é promovido para o provider."""
        class MockWriter:
            def __init__(self):
                self.items = []
            def write_candidate(self, cand):
                self.items.append(cand)
                return True

        writer = MockWriter()
        c = MemoryCandidate(
            fact="Segundo a documentação do projeto, a porta de serviço é 8080",
            source_uri="session://s1",
            confidence=0.9,
            classification=AssertionClassification(
                assertion_type=AssertionType.FACT_VERIFIED,
                confidence=0.9,
                evidence_refs=["session://s1#line42"],
            ),
        )
        res = self.router.process_candidate(c, writer)
        self.assertTrue(res)
        self.assertEqual(c.status, "consolidated")
        self.assertEqual(len(writer.items), 1)

    def test_canonical_lesson_writer_gate(self):
        """_CanonicalLessonWriter bloqueia HYPOTHESIS, FACT_UNVERIFIED e REFUTED antes de gravar no OKF."""
        okf_dir = self.path / "okf"
        writer = _CanonicalLessonWriter(
            okf_dir=okf_dir,
            session_id="s123456",
            title="Session Title",
        )

        # 1. HYPOTHESIS
        c_hyp = MemoryCandidate(
            fact="Hipótese: memória pode vazar se não fechar a conexão",
            source_uri="session://s1",
            confidence=0.9,
            classification=AssertionClassification(
                assertion_type=AssertionType.HYPOTHESIS,
                evidence_refs=["session://s1"],
            ),
        )
        self.assertFalse(writer.write_candidate(c_hyp))

        # 2. FACT_UNVERIFIED
        c_unv = MemoryCandidate(
            fact="O sistema consome 500MB de RAM",
            source_uri="session://s1",
            confidence=0.9,
            classification=AssertionClassification(
                assertion_type=AssertionType.FACT_UNVERIFIED,
                evidence_refs=["session://s1"],
            ),
        )
        self.assertFalse(writer.write_candidate(c_unv))

        # 3. REFUTED
        c_ref = MemoryCandidate(
            fact="Desmentido o boato de instabilidade no cluster",
            source_uri="session://s1",
            confidence=0.9,
            classification=AssertionClassification(
                assertion_type=AssertionType.REFUTED,
                evidence_refs=["session://s1"],
            ),
        )
        self.assertFalse(writer.write_candidate(c_ref))

        # 4. FACT_VERIFIED sem evidência
        c_no_ev = MemoryCandidate(
            fact="Segundo o relatório técnico, os testes passaram",
            source_uri="session://s1",
            confidence=0.9,
            classification=AssertionClassification(
                assertion_type=AssertionType.FACT_VERIFIED,
                evidence_refs=[],
            ),
        )
        self.assertFalse(writer.write_candidate(c_no_ev))

        # 5. FACT_VERIFIED com evidência
        c_ok = MemoryCandidate(
            fact="Segundo o log verificado, os testes passaram com sucesso",
            source_uri="session://s1",
            confidence=0.9,
            classification=AssertionClassification(
                assertion_type=AssertionType.FACT_VERIFIED,
                evidence_refs=["session://s1#log"],
            ),
        )
        self.assertTrue(writer.write_candidate(c_ok))
        self.assertIsNotNone(writer.last_written)
        assert writer.last_written is not None
        self.assertTrue(writer.last_written.exists())

    def test_reconciler_gate(self):
        """MemoryReconciler.reconcile_candidate bloqueia não-FACT_VERIFIED e sem evidência."""
        rec = MemoryReconciler(self.path / "reconciled.db")

        c_hyp = MemoryCandidate(
            fact="Talvez a arquitetura mude para gRPC",
            source_uri="session://s1",
            confidence=0.9,
            classification=AssertionClassification(
                assertion_type=AssertionType.HYPOTHESIS,
                evidence_refs=["session://s1"],
            ),
        )
        res_hyp = rec.reconcile_candidate(c_hyp)
        self.assertEqual(res_hyp["status"], "rejected")
        self.assertIn("HYPOTHESIS", res_hyp["reason"])

        c_unv = MemoryCandidate(
            fact="A arquitetura usa SQLite em staging",
            source_uri="session://s1",
            confidence=0.9,
            classification=AssertionClassification(
                assertion_type=AssertionType.FACT_UNVERIFIED,
                evidence_refs=["session://s1"],
            ),
        )
        res_unv = rec.reconcile_candidate(c_unv)
        self.assertEqual(res_unv["status"], "rejected")
        self.assertIn("FACT_UNVERIFIED", res_unv["reason"])

        c_no_ev = MemoryCandidate(
            fact="Segundo o líder técnico, a arquitetura usa Rust",
            source_uri="session://s1",
            confidence=0.9,
            classification=AssertionClassification(
                assertion_type=AssertionType.FACT_VERIFIED,
                evidence_refs=[],
            ),
        )
        res_no_ev = rec.reconcile_candidate(c_no_ev)
        self.assertEqual(res_no_ev["status"], "rejected")
        self.assertIn("sem referências de evidência", res_no_ev["reason"])

        c_ok = MemoryCandidate(
            fact="Segundo o documento de arquitetura, o banco principal é PostgreSQL",
            source_uri="session://s1",
            confidence=0.95,
            classification=AssertionClassification(
                assertion_type=AssertionType.FACT_VERIFIED,
                evidence_refs=["session://s1#arch"],
            ),
        )
        res_ok = rec.reconcile_candidate(c_ok)
        self.assertEqual(res_ok["status"], "active")


if __name__ == "__main__":
    unittest.main()
