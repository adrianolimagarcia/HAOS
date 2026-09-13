"""B3/B4 — Obsidian real + GraphRAG real (invariantes).

Obsidian:
(a) available() exige vault legível com .md; leituras sem vault -> erro.
(b) list/read/get_adr(ADR-<id>)/search (matcher stdlib determinístico);
    read_note rejeita path que escapa do vault.
(c) CLI seam (peer scriptado) substitui o matcher quando presente.

GraphRAG:
(d) construtor exige service_url OU index_dir (fail-closed).
(e) modo índice: entities.csv obrigatório; query local/global retorna entidades
    casadas por termos (>2 chars) + relationships; disponibilidade ligada ao
    arquivo; ausente -> available False e query levanta.
(f) modo serviço: /health => disponível; POST /query devolve response/entities;
    500 => GraphRAGRemoteError(status); sem health => fail-closed.
"""

import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hermes.platform.memory.obsidian import (
    ObsidianAdapter, ObsidianError,
)
from hermes.platform.memory.graphrag import (
    GraphRAGClient, GraphRAGError, GraphRAGRemoteError,
)


class TestObsidian(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "adrs").mkdir(parents=True)
        (self.vault / "proj").mkdir()
        (self.vault / "readme.md").write_text(
            "# Projeto\nDECIDED: usar OIDC para auth\n", encoding="utf-8")
        (self.vault / "adrs" / "ADR-001-auth.md").write_text(
            "# ADR-001\nStatus: accepted\nAuth via OIDC.", encoding="utf-8")
        (self.vault / "proj" / "notes.md").write_text(
            "notas soltas sobre deploy\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_available_and_listing(self):
        adapter = ObsidianAdapter(str(self.vault))
        self.assertTrue(adapter.available())
        notes = adapter.list_notes()
        self.assertEqual(notes, ["adrs/ADR-001-auth.md",
                                 "proj/notes.md", "readme.md"])

    def test_read_and_get_adr(self):
        adapter = ObsidianAdapter(str(self.vault))
        note = adapter.read_note("readme.md")
        self.assertIn("OIDC", note["content"])
        adr = adapter.get_adr("001")
        self.assertIsNotNone(adr)
        self.assertIn("ADR-001", adr["content"])
        self.assertEqual(adr["path"], "adrs/ADR-001-auth.md")
        self.assertIsNone(adr and adapter.get_adr("999"))

    def test_path_escape_rejected(self):
        adapter = ObsidianAdapter(str(self.vault))
        with self.assertRaises(ObsidianError):
            adapter.read_note("../secret.md")

    def test_unavailable_vault_fails_closed(self):
        adapter = ObsidianAdapter(str(self._tmp.name) + "/nope")
        self.assertFalse(adapter.available())
        with self.assertRaises(ObsidianError):
            adapter.read_note("readme.md")

    def test_search_stdlib_matcher(self):
        adapter = ObsidianAdapter(str(self.vault))
        hits = adapter.search("OIDC")
        self.assertIn("adrs/ADR-001-auth.md", hits)
        self.assertEqual(adapter.search("deploy"), ["proj/notes.md"])

    def test_search_with_cli_seam(self):
        fake = self._tmp.name + "/fake-rg"
        script = (
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "query = sys.argv[-2].lower()\n"
            "vault = sys.argv[-1]\n"
            "for root, _dirs, files in os.walk(vault):\n"
            "    for name in files:\n"
            "        if not name.endswith('.md'):\n"
            "            continue\n"
            "        path = os.path.join(root, name)\n"
            "        if query in open(path, encoding='utf-8').read().lower():\n"
            "            print(path)\n"
        )
        Path(fake).write_text(script, encoding="utf-8")
        Path(fake).chmod(Path(fake).stat().st_mode
                         | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        adapter = ObsidianAdapter(str(self.vault), cli=[fake])
        hits = adapter.search("accepted")
        self.assertEqual(hits, ["adrs/ADR-001-auth.md"])


def _serve(handler_cls, payload_fn):
    class Handler(BaseHTTPRequestHandler):
        def _do(self) -> None:
            status, payload = payload_fn(self.command, self.path, self)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            self._do()

        def do_POST(self) -> None:  # noqa: N802
            self._do()

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class TestGraphRAG(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _index(self):
        idx = self.root / "index"
        idx.mkdir()
        (idx / "entities.csv").write_text(
            "entity,type,description\n"
            "auth,component,OIDC auth flow\n"
            "gateway,service,HTTP ingress\n", encoding="utf-8")
        (idx / "relationships.csv").write_text(
            "source,target,kind\n"
            "gateway,auth,depends_on\n", encoding="utf-8")
        return str(idx)

    def test_constructor_fails_closed_without_mode(self):
        with self.assertRaises(GraphRAGError):
            GraphRAGClient()

    def test_index_available_and_query(self):
        client = GraphRAGClient(index_dir=self._index())
        self.assertTrue(client.available())
        out = client.query_local("qual auth usar?")
        self.assertEqual(out["mode"], "index")
        self.assertEqual(out["method"], "local")
        self.assertEqual(out["entities"][0]["entity"], "auth")
        self.assertEqual(out["relationships"][0]["target"], "auth")
        none = client.query_global("kkk qqq rrr")  # termos curtos/ausentes
        self.assertEqual(none["entities"], [])

    def test_index_missing_file_fails_closed(self):
        empty = self.root / "empty"
        empty.mkdir()
        client = GraphRAGClient(index_dir=str(empty))
        self.assertFalse(client.available())
        with self.assertRaises(GraphRAGError):
            client.query_local("auth")

    def test_service_health_and_query(self):
        def payload(command, path, handler):
            if command == "GET" and path == "/health":
                return 200, b'{"status":"ok"}'
            if command == "POST" and path == "/query":
                length = int(handler.headers.get("Content-Length") or 0)
                req = json.loads(handler.rfile.read(length).decode("utf-8"))
                return 200, json.dumps({
                    "response": "use OIDC",
                    "entities": [{"entity": "auth"}],
                    "sources": [{"source": "auth"}],
                }).encode("utf-8")
            return 404, b'{"error":"nf"}'

        server, base = _serve(object, payload)
        try:
            client = GraphRAGClient(service_url=base)
            self.assertTrue(client.available())
            self.assertEqual(client.mode, "service")
            out = client.query_local("auth")
            self.assertEqual(out["response"], "use OIDC")
            self.assertEqual(out["entities"][0]["entity"], "auth")
        finally:
            server.shutdown()
            server.server_close()

    def test_service_error_and_missing_health(self):
        def payload(command, path, handler):
            if command == "GET" and path == "/health":
                return 503, b'{"error":"down"}'
            if command == "POST" and path == "/query":
                return 500, b"boom"
            return 404, b"{}"

        server, base = _serve(object, payload)
        try:
            client = GraphRAGClient(service_url=base)
            self.assertFalse(client.available())
            with self.assertRaises(GraphRAGError):
                client.query_local("auth")
        finally:
            server.shutdown()
            server.server_close()

    def test_service_http_500_raises_remote(self):
        def payload(command, path, handler):
            if command == "GET" and path == "/health":
                return 200, b'{"status":"ok"}'
            return 500, b"boom"

        server, base = _serve(object, payload)
        try:
            client = GraphRAGClient(service_url=base)
            with self.assertRaises(GraphRAGRemoteError) as ctx:
                client.query_global("auth")
            self.assertEqual(ctx.exception.status, 500)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
