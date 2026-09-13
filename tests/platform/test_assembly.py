"""Assembléia do runtime (Fase 1 wire) — deep-research no registry (invariantes).

Contratos de comportamento:
(a) resolver de runtime resolve `deep-research` (agentic, research-worker) e
    mantém os defaults do kernel (git/code-intelligence) — assembly é aditivo;
(b) provider vivo no registry: sem fetcher -> probe available False e acquire
    levanta (fail-closed mesmo registrado); com fetcher injetado -> probe True
    e acquire devolve artefato complete com as citações do seam canônico.
"""

import asyncio
import unittest

from hermes.platform.capabilities.research.worker import (
    ResearchProvider, ResearchProviderError,
)
from hermes.platform.assembly import (
    build_capability_registry, build_capability_resolver,
)


class TestRuntimeAssembly(unittest.TestCase):
    def test_resolver_resolves_deep_research_and_keeps_defaults(self):
        resolver = build_capability_resolver()
        research = resolver.resolve_requirements(["deep-research"])
        self.assertIn("deep-research", research)
        self.assertEqual(research["deep-research"].execution_kind, "agentic")
        self.assertIn("research-worker", research["deep-research"].providers)
        # Aditivo: defaults do kernel continuam lá.
        defaults = resolver.resolve_requirements(
            ["git", "code-intelligence", "deep-research"]
        )
        self.assertEqual(
            set(defaults),
            {"git", "code-intelligence", "deep-research"},
        )

    def test_registered_without_fetcher_fails_closed(self):
        registry = build_capability_registry()
        provider = registry.get_provider("research-worker")
        self.assertIsInstance(provider, ResearchProvider)
        probe = asyncio.run(provider.probe())
        self.assertFalse(probe["available"])
        with self.assertRaises(ResearchProviderError):
            asyncio.run(provider.acquire({"question": "P?"}))

    def test_registered_with_fetcher_is_available(self):
        calls = []

        def fetch(question):
            calls.append(question)
            return [{"url": "https://ex.com/r", "title": "R",
                     "content": "conteudo", "score": 0.9}]

        registry = build_capability_registry(ResearchProvider(fetch))
        provider = registry.get_provider("research-worker")
        probe = asyncio.run(provider.probe())
        self.assertTrue(probe["available"])
        artifact = asyncio.run(provider.acquire({"question": "Q?"}))
        self.assertEqual(artifact.status, "complete")
        self.assertEqual(len(artifact.citations), 1)
        self.assertEqual(artifact.citations[0].url, "https://ex.com/r")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
