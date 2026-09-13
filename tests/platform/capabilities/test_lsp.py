"""Contract tests for the HAOS LSP capability (K6: "LSP real").

Hermetic style: unittest, nothing outside a TemporaryDirectory, no touch of
``~/.hermes``. The "real" language server is the scripted module
``_mock_lsp_server.py`` (stdlib only) spawned as a genuine subprocess with
``[sys.executable, _mock_lsp_server.py]``, so the transport exercised here is
the actual Content-Length framed JSON-RPC 2.0 wire over stdio.

Run from the repo root:  PYTHONPATH=. python3 tests/platform/capabilities/test_lsp.py
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

from hermes.platform.capabilities.lsp.manager import (
    LSPClient,
    LSPManager,
    LSPUnavailableError,
    StaticLSPClient,
)
from hermes.platform.capabilities.lsp.protocol import (
    classify_message,
    encode_message,
    make_notification,
    make_request,
)

_MOCK_SERVER = Path(__file__).resolve().parent / "_mock_lsp_server.py"
_SAMPLE_SOURCE = "class Sample:\n    pass\n"


def _server_command():
    return [sys.executable, str(_MOCK_SERVER)]


class TestProtocolFramer(unittest.TestCase):
    def test_encode_message_framing_matches_byte_length(self):
        payload = make_request(7, "initialize", {"capabilities": {}})
        framed = encode_message(payload)
        self.assertTrue(framed.startswith(b"Content-Length: "))
        header, _, body = framed.partition(b"\r\n\r\n")
        declared = int(header.split(b":", 1)[1].strip())
        self.assertEqual(declared, len(body))

    def test_classify_message_kinds(self):
        self.assertEqual(classify_message(make_request(1, "x", None)), ("request", 1))
        self.assertEqual(
            classify_message({"jsonrpc": "2.0", "id": 1, "result": None}),
            ("response", 1),
        )
        self.assertEqual(
            classify_message(make_notification("exit", None)),
            ("notification", "exit"),
        )
        self.assertEqual(classify_message({"nope": True}), ("invalid", None))


class _ClientTestCase(unittest.TestCase):
    """Shared scaffolding: temp workspace with sample.py, plus client cleanup."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.sample = self.workspace / "sample.py"
        self.sample.write_text(_SAMPLE_SOURCE, encoding="utf-8")
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            try:
                client.shutdown()
            except Exception:
                pass
        self._tmp.cleanup()

    def make_real_client(self):
        client = LSPClient(
            str(self.workspace), language="python", server_command=_server_command()
        )
        self.clients.append(client)
        return client


class TestRealLSPClient(_ClientTestCase):
    def test_start_handshake_and_running(self):
        client = self.make_real_client()
        client.start()
        self.assertTrue(client.running)
        self.assertEqual(client.server_info.get("name"), "mock-lsp")

    def test_get_document_symbols_via_wire(self):
        client = self.make_real_client()
        symbols = client.get_document_symbols("sample.py")
        self.assertTrue(len(symbols) >= 1)
        self.assertIn("Sample", [s["name"] for s in symbols])
        found = symbols[0]
        self.assertEqual(found["kind"], "Class")
        self.assertEqual(found["line"], 0)
        self.assertIn("Sample", self.sample.read_text(encoding="utf-8"))

    def test_find_references(self):
        client = self.make_real_client()
        client.get_document_symbols("sample.py")
        refs = client.find_references("Sample")
        self.assertTrue(len(refs) >= 1)
        self.assertTrue(all("file" in r and "line" in r for r in refs))
        self.assertEqual(refs[0]["file"], str(self.sample))
        self.assertEqual(refs[0]["line"], 0)

    def test_get_diagnostics_captures_pushed_notifications(self):
        client = self.make_real_client()
        client.get_document_symbols("sample.py")
        diag = client.get_diagnostics()
        self.assertIn("new_errors", diag)
        self.assertIn("warnings", diag)
        self.assertIn("details", diag)
        # Server pushes one error (severity 1) and one warning (severity 2).
        self.assertEqual(diag["new_errors"], 1)
        self.assertEqual(diag["warnings"], 1)
        self.assertEqual(len(diag["details"]), 2)

    def test_shutdown_terminates_process(self):
        client = self.make_real_client()
        client.start()
        self.assertTrue(client.running)
        proc = client._proc
        self.assertIsNotNone(proc)
        client.shutdown()
        self.assertFalse(client.running)
        # The subprocess must be reaped (no hang, bounded by timeouts above).
        deadline = time.time() + 10
        while proc is not None and proc.returncode is None and time.time() < deadline:
            time.sleep(0.05)
        if proc is not None:
            self.assertIsNotNone(proc.returncode)

    def test_context_manager(self):
        client = self.make_real_client()
        with client as live:
            self.assertTrue(live.running)
        self.assertFalse(client.running)

    def test_lazy_start_fail_fast_without_command(self):
        client = LSPClient(str(self.workspace), language="python")
        self.assertFalse(client.running)
        with self.assertRaises(LSPUnavailableError):
            client.get_document_symbols("sample.py")

    def test_missing_file_raises(self):
        client = self.make_real_client()
        with self.assertRaises(FileNotFoundError):
            client.get_document_symbols("missing.py")


class TestLSPManagerRouting(_ClientTestCase):
    def test_manager_without_command_returns_explicit_stub(self):
        mgr = LSPManager()
        client = mgr.get_client(str(self.workspace))
        self.assertIsInstance(client, StaticLSPClient)
        self.assertTrue(getattr(client, "is_stub", False))

    def test_stub_preserves_legacy_contract(self):
        mgr = LSPManager()
        client = mgr.get_client(str(self.workspace))
        symbols = client.get_document_symbols("test.py")
        self.assertTrue(len(symbols) > 0)
        self.assertTrue(all(set(s) >= {"name", "kind", "line"} for s in symbols))
        diag = client.get_diagnostics()
        self.assertEqual(diag["new_errors"], 0)

    def test_stub_is_memoized_per_workspace(self):
        mgr = LSPManager()
        first = mgr.get_client(str(self.workspace))
        second = mgr.get_client(str(self.workspace))
        self.assertIs(first, second)
        other = mgr.get_client(str(self.workspace / "other"))
        self.assertIsNot(first, other)

    def test_manager_with_command_returns_real_client(self):
        mgr = LSPManager()
        client = mgr.get_client(str(self.workspace), server_command=_server_command())
        self.assertIsInstance(client, LSPClient)
        self.assertFalse(getattr(client, "is_stub", False))

    def test_real_client_is_memoized_and_lazy(self):
        mgr = LSPManager()
        cmd = _server_command()
        first = mgr.get_client(str(self.workspace), server_command=cmd)
        second = mgr.get_client(str(self.workspace), server_command=cmd)
        self.assertIs(first, second)
        self.assertFalse(first.running)  # get_client must NOT auto-start


if __name__ == "__main__":
    unittest.main()
