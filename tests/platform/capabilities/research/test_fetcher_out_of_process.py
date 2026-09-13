"""D1 — Research fetcher out-of-process (forma B): invariantes do peer.

Contratos (nunca snapshots):
(a) SubprocessFetcher roda o peer REAL de subprocesso com uma pergunta no
    argv e parseia a linha JSON canônica {"results": [...]}; available()
    ligado à presença do executável (fail-closed).
(b) rc != 0 -> SubprocessFetcherError; stdout sem linha JSON -> erro;
    payload sem 'results' -> erro; max_results respeitado.
(c) O provider montado via register_research_fetcher_provider(peer_command)
    executa o fluxo REAL (decompose -> fetch -> aggregate) e devolve
    ResearchArtifact com citações do peer — sem fetcher/peer =>
    probe available False (fail-closed).
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from hermes.platform.capabilities.registry import CapabilityRegistry
from hermes.platform.capabilities.research.fetcher import (
    SubprocessFetcher, SubprocessFetcherError,
    make_research_provider, register_research_fetcher_provider,
)

_PEER = str(Path(__file__).resolve().parent / "_fake_fetch_peer.py")
try:
    os.chmod(_PEER, 0o755)
except OSError:
    pass


def _env(mode: str = "ok", custom_json: str = ""):
    env = dict(os.environ)
    env["HAOS_PEER_MODE"] = mode
    if custom_json:
        env["HAOS_PEER_JSON"] = custom_json
    return env


class TestSubprocessFetcher(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._old_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        self._tmp.cleanup()

    def test_fetch_runs_peer_and_parses(self):
        os.environ.update(_env())
        fetcher = SubprocessFetcher(_PEER)
        self.assertTrue(fetcher.available())
        results = fetcher("qual a melhor prática?")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://docs.example.com/1")
        self.assertIn("qual a melhor prática", results[0]["content"])

    def test_peer_nonzero_fails_closed(self):
        os.environ.update(_env("nonzero"))
        fetcher = SubprocessFetcher(_PEER)
        with self.assertRaises(SubprocessFetcherError):
            fetcher("pergunta?")

    def test_peer_empty_stdout_fails_closed(self):
        os.environ.update(_env("empty"))
        fetcher = SubprocessFetcher(_PEER)
        with self.assertRaises(SubprocessFetcherError):
            fetcher("pergunta?")

    def test_bad_json_fails_closed(self):
        os.environ.update(_env("bad_json"))
        fetcher = SubprocessFetcher(_PEER)
        with self.assertRaises(SubprocessFetcherError):
            fetcher("pergunta?")

    def test_missing_peer_unavailable(self):
        fetcher = SubprocessFetcher("/nonexistent/fetch-peer")
        self.assertFalse(fetcher.available())
        with self.assertRaises(SubprocessFetcherError):
            fetcher("pergunta?")

    def test_max_results_capped(self):
        os.environ.update(_env())
        fetcher = SubprocessFetcher(_PEER, max_results=1)
        self.assertEqual(len(fetcher("pergunta?")), 1)


class TestProviderOutOfProcess(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)
        self.registry = CapabilityRegistry()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def test_registered_provider_runs_real_pipeline(self):
        os.environ.update(_env())
        provider = register_research_fetcher_provider(
            self.registry, peer_command=_PEER)
        probe = asyncio.run(provider.probe())
        self.assertTrue(probe["available"])
        artifact = asyncio.run(provider.acquire({"question": "buscar algo?"}))
        self.assertEqual(artifact.status, "complete")
        self.assertEqual(artifact.question, "buscar algo?")
        self.assertGreaterEqual(len(artifact.findings), 1)
        urls = [c.url for f in artifact.findings for c in f.citations]
        self.assertIn("https://docs.example.com/1", urls)

    def test_no_fetcher_fails_closed(self):
        provider = register_research_fetcher_provider(self.registry)
        probe = asyncio.run(provider.probe())
        self.assertFalse(probe["available"])


if __name__ == "__main__":
    unittest.main()
