"""VaultFTSIndex — índice FTS5 trigram sobre vault Obsidian (invariantes).

Valida:
1. local do DB default sob ``HERMES_HOME/memory/vault_fts/<sha1 do vault>.db``
   (``index_dir``), sem hardcode de ~/.hermes.
2. sync incremental por mtime: nota adicionada/alterada/removida refletida após
   novo sync; sync idempotente.
3. search == substring scan exato (oráculo independente lendo os arquivos):
   case-insensitive, substring no meio da palavra, ordenação estável, fallback
   <3 chars, queries não-ASCII/pontuação/aspas.
4. campos: ``match_title`` reproduz a semântica fabric (título = frontmatter
   ``title`` ou stem); ``match_content`` reproduz a semântica B3 (só conteúdo).
5. parsing compartilhado de frontmatter/título (fonte única fabric + índice).
6. integração B3: ``ObsidianAdapter.search`` com índice == matcher stdlib, e
   igual ao fallback (índice indisponível) no conjunto de resultados.
"""

import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hermes_constants import get_hermes_home
from hermes.platform.memory.obsidian import ObsidianAdapter
from hermes.platform.memory.vault_fts import (
    VaultFTSIndex,
    index_dir,
    obsidian_note_title,
    parse_obsidian_frontmatter,
)


def _write_vault(root: Path) -> None:
    """Vault fixo: notas com e sem frontmatter, subdiretórios, stem só no nome."""
    (root / "adrs").mkdir(parents=True)
    (root / "docs" / "sub").mkdir(parents=True)
    (root / "readme.md").write_text(
        "# Projeto\nDECIDED: usar OIDC para auth no gateway.\n", encoding="utf-8")
    (root / "adrs" / "ADR-001-auth.md").write_text(
        "# ADR-001\n---\nStatus: accepted\n---\nAuth via OIDC flow.\n", encoding="utf-8")
    (root / "docs" / "notes.md").write_text(
        "notas soltas sobre deploy e cluster\n", encoding="utf-8")
    (root / "docs" / "sub" / "deep.md").write_text(
        "arquitetura de referência sem menção\n", encoding="utf-8")
    # stem só no título (sem frontmatter): 'KappaDocs' jamais aparece no conteúdo.
    (root / "docs" / "KappaDocs.md").write_text(
        "miscelânea apenas, sem nenhuma menção ao nome do arquivo\n", encoding="utf-8")
    (root / "docs" / "quote.md").write_text(
        'o guia disse "hello world" e seguiu adiante\n', encoding="utf-8")
    (root / "docs" / "fm.md").write_text(
        "---\ntitle: Encrypted Ledger\n---\ncorpo do documento com mapeamento\n",
        encoding="utf-8")


def _note_title(content: str, stem: str) -> str:
    """Título histórico do adapter (oráculo independente do módulo sob teste)."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            front = {}
            for line in parts[1].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    front[key.strip().lower()] = value.strip()
            return front.get("title", stem)
    return stem


class TestVaultFTSIndex(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        _write_vault(self.vault)

    def tearDown(self):
        self._tmp.cleanup()

    def _index(self, vault=None):
        db = Path(self._tmp.name) / "idx.db"
        return VaultFTSIndex(vault or self.vault, db_path=db)

    # oráculo independente: lê os arquivos e aplica substring (mesma semântica
    # que o matcher histórico — title e content checados separadamente, sem
    # casar "através" da fronteira entre os dois), sem tocar no módulo sob teste.
    def _oracle(self, query, match_title=True, vault=None):
        vault = vault or self.vault
        lowered = query.lower()
        hits = []
        for path in vault.rglob("*.md"):
            if not path.is_file():
                continue
            rel = path.relative_to(vault).as_posix()
            content = path.read_text(encoding="utf-8", errors="replace")
            matched = lowered in content.lower()
            if match_title and not matched:
                matched = lowered in _note_title(content, path.stem).lower()
            if matched:
                hits.append(rel)
        return sorted(hits)

    # ------------------------------------------------------------------ #
    def test_index_dir_default_location_keyed_by_vault_sha1(self):
        expected = (
            Path(get_hermes_home()) / "memory" / "vault_fts"
            / f"{hashlib.sha1(os.fsencode(os.path.abspath(str(self.vault)))).hexdigest()}.db"
        )
        self.assertEqual(index_dir(self.vault), expected)
        self.assertEqual(index_dir(str(self.vault)), expected)  # str e Path idênticos

    def test_default_db_created_under_hermes_home(self):
        idx = VaultFTSIndex(self.vault)  # db_path default -> HERMES_HOME isolado
        try:
            self.assertTrue(idx.db_path.startswith(str(Path(get_hermes_home()))))
            self.assertTrue(Path(idx.db_path).parent.is_dir())
            idx.sync()
            self.assertTrue(Path(idx.db_path).exists())
        finally:
            idx.close()

    def test_search_substring_case_insensitive_and_stable(self):
        idx = self._index()
        # substring no meio da palavra
        self.assertEqual(idx.search("hitect"), [])  # não existe
        self.assertEqual(idx.search("cluster"), ["docs/notes.md"])
        self.assertEqual(idx.search("luste"), ["docs/notes.md"])
        self.assertEqual(idx.search("oidc"),
                         ["adrs/ADR-001-auth.md", "readme.md"])
        self.assertEqual(idx.search("OIDC"), idx.search("oIdC"))  # case-insensitive
        # ordenação estável (POSIX, sorted)
        hits = idx.search("a")
        self.assertEqual(hits, sorted(hits))
        self.assertEqual(idx.search("oidc"), idx.search("oidc"))

    def test_search_matches_plain_substring_oracle(self):
        idx = self._index()
        queries = [
            "oidc", "OIDC", "deploy", "cluster", "zzz", "a", "no", "au",
            "ADR-001", "001", "adr", "kappa", "kappadocs", "doc", "docs/sub",
            "--", "...", "aqui", "menção", "referência", "e c", "deploy e cluster",
            "# Projeto", "Gateway", "gw", '"hello"', 'disse "hell', "gui",
        ]
        for query in queries:
            got = idx.search(query)
            expected = self._oracle(query, match_title=True)
            self.assertEqual(got, expected, f"fabric semantics query={query!r}")

    def test_search_content_only_matches_b3_oracle(self):
        idx = self._index()
        queries = ["oidc", "OIDC", "deploy", "kappa", "kappadocs", "no",
                   "menção", "zz", "cluster", '"hello"', "--"]
        for query in queries:
            got = idx.search(query, match_title=False)
            expected = self._oracle(query, match_title=False)
            self.assertEqual(got, expected, f"b3 semantics query={query!r}")

    def test_title_match_only_via_stem_fallback(self):
        idx = self._index()
        # 'KappaDocs' está só no nome do arquivo (stem = título sem frontmatter)
        self.assertNotIn("kappadocs", self.vault.joinpath(
            "docs", "KappaDocs.md").read_text(encoding="utf-8").lower())
        self.assertEqual(idx.search("kappadocs", match_title=False), [])
        self.assertEqual(idx.search("kappadocs", match_title=True),
                         ["docs/KappaDocs.md"])

    def test_incremental_sync_add_change_remove_by_mtime(self):
        idx = self._index()
        idx.sync()
        note = self.vault / "docs" / "new.md"
        note.write_text("conteúdo com zebra listrada\n", encoding="utf-8")
        idx.sync()
        self.assertEqual(idx.search("zebra"), ["docs/new.md"])

        # altera o conteúdo: token some, outro aparece (mtime muda)
        note.write_text("conteúdo com raposa veloz\n", encoding="utf-8")
        idx.sync()
        self.assertEqual(idx.search("raposa"), ["docs/new.md"])
        self.assertEqual(idx.search("zebra"), [])
        # notas não tocadas continuam lá
        self.assertEqual(idx.search("cluster"), ["docs/notes.md"])

        # remove o arquivo
        note.unlink()
        idx.sync()
        self.assertEqual(idx.search("raposa"), [])
        self.assertEqual(idx.search("cluster"), ["docs/notes.md"])

    def test_sync_is_idempotent(self):
        idx = self._index()
        idx.sync()
        first = idx.search("oidc")
        idx.sync()  # segundo sync sem mudanças não altera resultados
        self.assertEqual(idx.search("oidc"), first)

    def test_short_and_non_ascii_queries_use_exact_fallback(self):
        idx = self._index()
        (self.vault / "docs" / "u.md").write_text(
            "Überraschung im frühjahr\n", encoding="utf-8")
        idx.sync()
        for query in ["über", "Überraschung", "früh", "u", "ü"]:
            self.assertEqual(idx.search(query), self._oracle(query),
                             f"query={query!r}")

    def test_close_marks_index_unusable(self):
        idx = self._index()
        idx.close()
        with self.assertRaises(RuntimeError):
            idx.search("oidc")
        with self.assertRaises(RuntimeError):
            idx.sync()

    def test_reopen_after_gap_rebuilds_index_from_shadow(self):
        """Trigger gap (linhas na shadow sem cobertura FTS) -> rebuild no open.

        Um índice externo cujos triggers sumiram tem um buraco de extensão
        desconhecida; reabrir precisa reconstruir a partir da shadow — nunca
        servir resultados incompletos silenciosamente.
        """
        note = self.vault / "docs" / "gap.md"
        note.write_text("corpo inicial do gap\n", encoding="utf-8")
        idx = self._index()
        idx.sync()
        db = Path(self._tmp.name) / "idx.db"

        # quebra a cobertura: some com os triggers e altera arquivo + shadow
        # sem tocar no índice FTS -> FTS guarda o texto antigo (buraco)
        note.write_text("conteudo exclusivo da nota gap\n", encoding="utf-8")
        mtime = note.stat().st_mtime_ns
        raw = sqlite3.connect(str(db))
        try:
            for trigger in ("notes_fts_insert", "notes_fts_update",
                            "notes_fts_delete"):
                raw.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            raw.execute(
                "UPDATE notes SET mtime = ?, title = 'Gap', content = ? "
                "WHERE path = 'docs/gap.md'",
                (mtime, "conteudo exclusivo da nota gap"))
            raw.commit()
        finally:
            raw.close()

        reopened = VaultFTSIndex(self.vault, db_path=db)
        try:
            # o rebuild tomou a shadow como verdade (texto novo encontrável)
            self.assertEqual(reopened.search("exclusivo"), ["docs/gap.md"])
            # notas intactas continuam pesquisáveis após o rebuild
            self.assertEqual(reopened.search("cluster"), ["docs/notes.md"])
            # triggers reinstalados: escrita pós-reopen continua indexada
            (self.vault / "after.md").write_text(
                "depois do rebuild\n", encoding="utf-8")
            reopened.sync()
            self.assertEqual(reopened.search("depois"), ["after.md"])
        finally:
            reopened.close()

    def test_reopen_keeps_index_consistent(self):
        idx = self._index()
        idx.sync()
        before = idx.search("oidc")
        idx.close()

        reopened = VaultFTSIndex(self.vault, db_path=Path(self._tmp.name) / "idx.db")
        try:
            # sem gap, reabrir não duplica nem perde resultados
            self.assertEqual(reopened.search("oidc"), before)
            self.assertEqual(reopened.search("oidc"), reopened.search("oidc"))
        finally:
            reopened.close()

    def test_empty_query_lists_all_sorted(self):
        idx = self._index()
        idx.sync()
        expected = sorted(
            str(p.relative_to(self.vault)).replace(os.sep, "/")
            for p in self.vault.rglob("*.md") if p.is_file()
        )
        self.assertEqual(idx.search(""), expected)


class TestObsidianFrontmatterShared(unittest.TestCase):
    """O parser de frontmatter/título é fonte única (adapter fabric + índice)."""

    def test_no_frontmatter(self):
        front, body = parse_obsidian_frontmatter("apenas corpo")
        self.assertEqual(front, {})
        self.assertEqual(body, "apenas corpo")
        self.assertEqual(obsidian_note_title("apenas corpo", "stem"), "stem")

    def test_frontmatter_parse_and_title_precedence(self):
        content = "---\nTitle: My Note\nType: adr\n---\ncorpo aqui\n"
        front, body = parse_obsidian_frontmatter(content)
        self.assertEqual(front, {"title": "My Note", "type": "adr"})
        self.assertEqual(body, "corpo aqui")
        self.assertEqual(obsidian_note_title(content, "stem"), "My Note")

    def test_empty_title_key_wins_over_stem(self):
        # mesmo comportamento do adapter: chave presente e vazia -> '' (não stem)
        content = "---\ntitle:\n---\ncorpo\n"
        self.assertEqual(obsidian_note_title(content, "stem"), "")

    def test_missing_title_key_falls_back_to_stem(self):
        content = "---\ntype: adr\n---\ncorpo\n"
        self.assertEqual(obsidian_note_title(content, "stem"), "stem")


class TestB3ObsidianSearchWithIndex(unittest.TestCase):
    """Integração B3: search() via índice == matcher stdlib / fallback."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        _write_vault(self.vault)

    def tearDown(self):
        self._tmp.cleanup()

    def _stdlib_expected(self, query):
        lowered = query.lower()
        return sorted(
            str(p.relative_to(self.vault)).replace(os.sep, "/")
            for p in self.vault.rglob("*.md")
            if p.is_file() and lowered in p.read_text(
                encoding="utf-8", errors="replace").lower()
        )

    def test_search_via_index_matches_stdlib_matcher(self):
        adapter = ObsidianAdapter(str(self.vault))
        for query in ["OIDC", "deploy", "cluster", "kappa", "kappadocs",
                      "a", "au", "no", "--", "menção", "zzz"]:
            self.assertEqual(adapter.search(query),
                             self._stdlib_expected(query), f"query={query!r}")

    def test_search_with_index_equals_search_without_index(self):
        with_index = ObsidianAdapter(str(self.vault))
        expected_via_index = with_index.search("OIDC")
        # força o fallback: índice indisponível -> matcher stdlib
        with mock.patch.object(VaultFTSIndex, "__init__",
                               side_effect=OSError("no index")):
            fallback = ObsidianAdapter(str(self.vault))
            expected_via_fallback = fallback.search("OIDC")
        self.assertEqual(sorted(expected_via_index), sorted(expected_via_fallback))
        self.assertGreater(len(expected_via_index), 0)


if __name__ == "__main__":
    unittest.main()
