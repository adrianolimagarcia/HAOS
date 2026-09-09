"""Integração ObsidianAdapter fabric (``retrieve``) com o índice FTS5 do vault.

Valida o contrato observável do adapter:
1. ``retrieve(query)`` devolve os mesmos itens com o índice ativo e com o
   fallback histórico (rglob+substring) — conjunto e conteúdo idênticos.
2. Nota escrita no vault e re-consultada reflete na hora (refresh por mtime).
3. Índice indisponível (ex.: sem escrita no HERMES_HOME) -> fallback exato,
   retrieve nunca levanta por causa do índice.
4. Queries vazias continuam listando todas as notas (caminho histórico).
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hermes.platform.context.memory.obsidian import ObsidianAdapter
from hermes.platform.memory.vault_fts import VaultFTSIndex


def _populate(vault: Path) -> None:
    (vault / "20-Architecture").mkdir(parents=True)
    (vault / "docs").mkdir()
    (vault / "20-Architecture" / "ADR-018.md").write_text(
        "---\ntitle: ADR-018 Protocol Fabric\ntype: architecture_decision\n---\n"
        "Architecture decision details.\n", encoding="utf-8")
    (vault / "docs" / "notes.md").write_text(
        "deploy com OIDC no gateway\n", encoding="utf-8")
    # sem frontmatter: 'Cometa-X' só casa pelo título (stem)
    (vault / "docs" / "Cometa-X.md").write_text(
        "texto trivial sem a palavra-chave\n", encoding="utf-8")


def _keyed(retrieved):
    return sorted((item.source_uri, item.title, item.content) for item in retrieved)


class TestFabricObsidianRetrieveFTS(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        _populate(self.vault)

    def tearDown(self):
        self._tmp.cleanup()

    def test_retrieve_identical_with_and_without_index(self):
        queries = ["ADR-018", "oidc", "OIDC", "deploy", "trivial", "zzz",
                   "st", "e", "gateway", "cometa-x", "arquitetura", "a"]
        # fallback histórico: índice impossível de construir
        with mock.patch.object(VaultFTSIndex, "__init__",
                               side_effect=OSError("no index")):
            fallback = ObsidianAdapter(self.vault)
            fallback_results = {
                q: _keyed(fallback.retrieve(q)) for q in queries
            }
        # índice real (db default sob HERMES_HOME isolado do conftest)
        with_index = ObsidianAdapter(self.vault)
        index_results = {
            q: _keyed(with_index.retrieve(q)) for q in queries
        }
        for query in queries:
            self.assertEqual(index_results[query], fallback_results[query],
                             f"query={query!r}")

        # sanidade: os dois caminhos realmente acharam o esperado
        self.assertEqual(len(index_results["ADR-018"]), 1)
        self.assertEqual(index_results["ADR-018"][0][0], "obsidian://20-Architecture/ADR-018.md")
        self.assertEqual(len(index_results["cometa-x"]), 1)  # casa pelo stem
        self.assertEqual(index_results["zzz"], [])
        # 'trivial' está no conteúdo da nota Cometa-X (sem frontmatter)
        self.assertEqual([k[0] for k in index_results["trivial"]],
                         ["obsidian://docs/Cometa-X.md"])
        # casa pelo stem também no fallback (title == stem)
        self.assertEqual(len(fallback_results["cometa-x"]), 1)

    def test_empty_query_lists_all_notes(self):
        with mock.patch.object(VaultFTSIndex, "__init__",
                               side_effect=OSError("no index")):
            fallback = ObsidianAdapter(self.vault)
            all_fallback = _keyed(fallback.retrieve())
        with_index = ObsidianAdapter(self.vault)
        all_index = _keyed(with_index.retrieve())
        self.assertEqual(all_index, all_fallback)
        self.assertEqual(len(all_index), 3)

    def test_written_note_is_found_after_requery(self):
        adapter = ObsidianAdapter(self.vault)
        adapter.write_note(
            "20-Architecture/ADR-019.md",
            title="ADR-019 Kafka Event Backbone",
            content="Decision: adopt Kafka as the event backbone.",
            doc_type="architecture_decision",
            metadata={"status": "accepted"},
        )
        hits = adapter.retrieve("Kafka")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].id, "obsidian-ADR-019")

        # overwrite remove o token (do conteúdo E do título) e introduz outro
        # -> re-consulta reflete a mudança (refresh incremental por mtime)
        adapter.write_note(
            "20-Architecture/ADR-019.md",
            title="ADR-019 Event Backbone",
            content="Decision: RabbitMQ replaces the backbone now.",
            doc_type="architecture_decision",
            metadata={"status": "superseded"},
        )
        self.assertEqual(adapter.retrieve("Kafka"), [])
        hits = adapter.retrieve("RabbitMQ")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].metadata.get("status"), "superseded")

    def test_index_unavailable_falls_back_never_raises(self):
        # mesmo cenário do fallback, mas varrendo várias queries: retrieve não
        # pode levantar só porque o índice falhou ao ser construído/consultado.
        adapter = ObsidianAdapter(self.vault)
        adapter._fts_idx = None
        with mock.patch.object(VaultFTSIndex, "__init__",
                               side_effect=PermissionError("read-only home")):
            for query in ["oidc", "deploy", "x", ""]:
                items = adapter.retrieve(query)
                self.assertIsInstance(items, list)


if __name__ == "__main__":
    unittest.main()
