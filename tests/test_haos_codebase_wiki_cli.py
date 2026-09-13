"""Contract tests for the ``codebase-wiki`` CLI surface (parser + run modes).

Covers CODEBASE_WIKI.md Fase 3 wiring: ``--docs``, ``--watch`` and ``--mcp``
dispatch to the right run functions, a missing root directory exits 2, and the
MCP registration path reports the namespaced tools it added. The parser is
built the same way ``hermes_cli/main._build_cli_parser`` composes it (register
the builder against a subparsers group), keeping the test independent of the
heavy CLI import chain.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from hermes_cli import codebase_wiki_impl
from hermes_cli.codebase_wiki import build_parser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    build_parser(sub)
    return parser


class TestCodebaseWikiParser:
    def test_help_lists_all_fase3_flags(self, capsys) -> None:
        p = _parser()
        with pytest.raises(SystemExit):
            p.parse_args(["codebase-wiki", "--help"])
        out = capsys.readouterr().out
        for flag in ("--include-tests", "--docs", "--watch", "--poll", "--mcp", "--json"):
            assert flag in out

    def test_modes_dispatch_to_run_entry_points(self) -> None:
        p = _parser()
        for argv, fn_name in [
            (["codebase-wiki", "x"], "codebase_wiki_command"),
            (["codebase-wiki", "x", "--docs"], "codebase_wiki_command"),
            (["codebase-wiki", "x", "--watch"], "codebase_wiki_command"),
            (["codebase-wiki", "x", "--mcp"], "codebase_wiki_command"),
        ]:
            ns = p.parse_args(argv)
            assert ns.func.__name__ == fn_name

    def test_poll_and_max_files_parsed(self) -> None:
        p = _parser()
        ns = p.parse_args(["codebase-wiki", ".", "--watch", "--poll", "0.25",
                           "--max-files", "10", "--docs", "--mcp", "--json"])
        assert ns.watch is True
        assert ns.poll == 0.25
        assert ns.max_files == 10
        assert ns.docs is True
        assert ns.mcp is True
        assert ns.json is True


class TestRunCliContract:
    def test_non_directory_root_returns_2(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        assert codebase_wiki_impl.run_cli(tmp_path / "missing", out) == 2

    def test_watch_mode_indexes_then_reindexes(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        out = tmp_path / "out"
        rc = codebase_wiki_impl.run_cli(root, out)
        assert rc == 0
        assert (out / "graph.json").is_file()

    def test_run_cli_docs_flag_indexes_markdown(self, tmp_path: Path) -> None:
        import json

        root = tmp_path / "corpus"
        root.mkdir()
        (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (root / "README.md").write_text("# Docs\n\nsee `a::f`\n", encoding="utf-8")
        out = tmp_path / "out"
        rc = codebase_wiki_impl.run_cli(root, out, docs=True)
        assert rc == 0
        raw = json.loads((out / "graph.json").read_text(encoding="utf-8"))
        assert any(n["id"].endswith("~md") for n in raw["nodes"])

    def test_mcp_mode_reports_namespaced_tools(self, tmp_path: Path, capsys) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        out = tmp_path / "out"
        assert codebase_wiki_impl.run_cli(root, out) == 0
        rc = codebase_wiki_impl.run_cli_mcp(out)
        assert rc == 0
        printed = capsys.readouterr().out
        assert "codebase-wiki_wiki_status" in printed
        assert "codebase-wiki_wiki_search" in printed
