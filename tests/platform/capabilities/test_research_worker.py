"""Worker de pesquisa (Fase 1, DeerFlow) — invariantes.

Contratos de comportamento (nunca snapshots): limites do decompose, pureza,
roundtrip JSON do artefato (inclui sub-perguntas/citações/evidence), contrato
probe/acquire/release com fetcher scriptado no seam canônico {url,title,
content,score}, fail-closed sem fetcher, dedupe de citações na agregação e
compatibilidade do evidence com proposal["evidence"] do Ouroboros.
"""

import asyncio
import json
import unittest

from hermes.platform.capabilities.registry import CapabilityRegistry
from hermes.platform.capabilities.research.models import (
    ResearchArtifact, ResearchQuestion, Finding, Citation,
)
from hermes.platform.capabilities.research.aggregate import aggregate
from hermes.platform.capabilities.research.worker import (
    ResearchProvider, ResearchProviderError, decompose,
    register_research_provider,
)


class TestDecompose(unittest.TestCase):
    def test_decompose_bounds(self):
        for max_q in (1, 3, 5):
            qs = decompose("Pergunta?", max_sub_questions=max_q)
            self.assertGreaterEqual(len(qs), 1)
            self.assertLessEqual(len(qs), max_q)
        # vazio/whitespace e max < 1 -> [] (total, sem erro)
        self.assertEqual(decompose("   ", max_sub_questions=3), [])
        self.assertEqual(decompose("Pergunta?", max_sub_questions=0), [])
        self.assertEqual(decompose("Pergunta?", max_sub_questions=-1), [])

    def test_decompose_purity(self):
        a = decompose("P?")
        b = decompose("P?")
        self.assertEqual(a, b)
        self.assertEqual(a[0].depth, 0)
        self.assertEqual(a[0].need_search, True)


class TestArtifactRoundtrip(unittest.TestCase):
    def test_artifact_roundtrip_json(self):
        q = ResearchQuestion(question="sub?", depth=1)
        artifact = ResearchArtifact(
            artifact_id="art-1",
            question="raiz?",
            findings=[
                Finding(
                    question="sub?",
                    claim="achado",
                    citations=[
                        Citation(url="https://ex.com/a", title="A",
                                 relevance_score=0.9)
                    ],
                    produced_by="research-worker",
                )
            ],
            citations=[Citation(url="https://ex.com/a", title="A",
                                relevance_score=0.9)],
            confidence=0.9,
            status="complete",
            produced_by="research-worker",
            evidence={"findings": [{"claim": "achado"}]},
            model="default",
        )
        raw = json.dumps(artifact.to_dict())
        restored = ResearchArtifact.from_dict(json.loads(raw))
        self.assertEqual(restored.artifact_id, "art-1")
        self.assertEqual(restored.question, "raiz?")
        self.assertEqual(len(restored.findings), 1)
        self.assertEqual(restored.findings[0].claim, "achado")
        self.assertEqual(restored.citations[0].url, "https://ex.com/a")
        self.assertEqual(restored.evidence["findings"][0]["claim"], "achado")

    def test_citation_id_derived_and_deterministic(self):
        c1 = Citation(url="https://ex.com/x")
        c2 = Citation(url="https://ex.com/x")
        self.assertEqual(c1.id, c2.id)
        self.assertEqual(len(c1.id), 12)
        # domain derivado automaticamente do url
        self.assertEqual(c1.domain, "ex.com")


class TestResearchProvider(unittest.TestCase):
    def _fetcher(self, calls, canned):
        def fetch(question):
            calls.append(question)
            return canned.get(question, [])
        return fetch

    def test_probe_acquire_release_with_scripted_fetcher(self):
        calls = []
        canned = {
            "P?": [
                {"url": "https://ex.com/1", "title": "Um", "content": "texto",
                 "score": 0.8},
                {"url": "https://ex.com/2", "title": "Dois", "content": "x",
                 "score": 0.5},
            ],
        }
        provider = ResearchProvider(self._fetcher(calls, canned))
        probe = asyncio.run(provider.probe())
        self.assertTrue(probe["available"])
        self.assertEqual(calls, [])  # probe NÃO invoca o fetcher

        artifact = asyncio.run(provider.acquire({"question": "P?"}))
        self.assertEqual(artifact.status, "complete")
        self.assertEqual(artifact.question, "P?")
        self.assertEqual(len(artifact.findings), 1)
        urls = {c.url for c in artifact.citations}
        self.assertIn("https://ex.com/1", urls)
        self.assertIn("https://ex.com/2", urls)
        self.assertEqual(len(calls), 1)

        asyncio.run(provider.release(artifact))
        artifact2 = asyncio.run(provider.acquire({"question": "P?"}))
        self.assertEqual(artifact2.status, "complete")
        self.assertEqual(len(calls), 2)  # provider reutilizável

    def test_missing_fetcher_fails_closed(self):
        provider = ResearchProvider()
        probe = asyncio.run(provider.probe())
        self.assertFalse(probe["available"])
        with self.assertRaises(ResearchProviderError) as ctx:
            asyncio.run(provider.acquire({"question": "P?"}))
        self.assertIn("fetcher", str(ctx.exception))

    def test_empty_question_returns_failed_artifact(self):
        provider = ResearchProvider(fetcher=lambda q: [])
        artifact = asyncio.run(provider.acquire({"question": "  "}))
        self.assertEqual(artifact.status, "failed")
        self.assertIn("error", artifact.evidence)

    def test_async_fetcher_accepted(self):
        async def fetch(_q):
            return [{"url": "https://ex.com/a", "title": "A",
                     "content": "c", "score": 0.7}]
        provider = ResearchProvider(fetch)
        artifact = asyncio.run(provider.acquire({"question": "P?"}))
        self.assertEqual(artifact.status, "complete")
        self.assertEqual(len(artifact.citations), 1)

    def test_dedupe_across_sub_questions(self):
        # decompose_fn injetada com DUAS sub-perguntas retornando a MESMA url:
        # a url aparece UMA única vez nas citações agregadas.
        def two_sub(question, **kwargs):
            return [
                ResearchQuestion(question="P1?", step_type="research"),
                ResearchQuestion(question="P2?", step_type="research"),
            ]
        canned = {
            "P1?": [{"url": "https://ex.com/u", "title": "U1",
                     "content": "c1", "score": 0.4}],
            "P2?": [{"url": "https://ex.com/u", "title": "U2 mais longa",
                     "content": "c2 mais longa", "score": 0.9}],
        }
        provider = ResearchProvider(
            lambda q: canned.get(q, []), decompose_fn=two_sub
        )
        artifact = asyncio.run(provider.acquire({"question": "P?"}))
        self.assertEqual(len(artifact.findings), 2)
        self.assertEqual(len(artifact.citations), 1)
        winner = artifact.citations[0]
        self.assertEqual(winner.relevance_score, 0.9)  # maior relevância vence
        self.assertEqual(winner.title, "U2 mais longa")


class TestAggregate(unittest.TestCase):
    def test_aggregate_pure_and_evidence_slot(self):
        artifact = ResearchArtifact(
            artifact_id="art-a", question="Q?",
            findings=[
                Finding(question="Q1?", claim="c1",
                        citations=[Citation(url="https://e.com/1", title="t1")],
                        confidence=0.5),
                Finding(question="Q2?", claim="c2",
                        citations=[Citation(url="https://e.com/1", title="t1"),
                                   Citation(url="https://e.com/2", title="t2",
                                            relevance_score=0.9)],
                        confidence=1.0),
            ],
            produced_by="research-worker",
        )
        original_status = artifact.status
        out = aggregate(artifact)
        # Pura: entrada inalterada.
        self.assertEqual(artifact.status, original_status)
        self.assertEqual(len(artifact.citations), 0)
        # Saída: resposta única contém cada claim; citações dedupe por url.
        self.assertIsNotNone(out.answer)
        self.assertIn("c1", out.answer)
        self.assertIn("c2", out.answer)
        self.assertEqual(len(out.citations), 2)
        self.assertEqual(out.status, "complete")
        self.assertEqual(round(out.confidence, 2), 0.75)
        # Slot Ouroboros: evidence com as chaves esperadas (relação, não valor).
        self.assertTrue(
            {"findings", "citations", "confidence"} <= set(out.evidence)
        )
        self.assertEqual(
            len(out.evidence["citations"]), 2
        )

    def test_aggregate_without_findings_stays_draft(self):
        artifact = ResearchArtifact(artifact_id="x", question="Q?",
                                    produced_by="p")
        out = aggregate(artifact)
        self.assertEqual(out.status, "draft")
        self.assertIsNone(out.answer)
        self.assertEqual(out.evidence, {})


class TestRegistration(unittest.TestCase):
    def test_register_research_provider(self):
        registry = CapabilityRegistry()
        provider = register_research_provider(registry)
        cap = registry.get("deep-research")
        self.assertIsNotNone(cap)
        self.assertEqual(cap.execution_kind, "agentic")
        self.assertIn(provider.provider_id, cap.providers)
        self.assertIs(registry.get_provider(provider.provider_id), provider)
        self.assertIn("decompose", cap.features)


if __name__ == "__main__":
    unittest.main()
