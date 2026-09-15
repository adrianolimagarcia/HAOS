"""Tests for the optional Tree-sitter path in the code knowledge graph.

`CodeSymbolGraph` indexes Python with the stdlib ``ast`` and every other language through
`treesitter_symbols`. The grammar pack is an opt-in extra, so the contracts that must hold
EVERYWHERE are the graceful-degradation ones; the real-grammar tests run wherever the pack
is installed and skip cleanly where it is not.
"""

from pathlib import Path

import pytest

from hermes.platform.capabilities.lsp import treesitter_symbols
from hermes.platform.capabilities.lsp.unified_intelligence import CodeSymbolGraph

TS_SNIPPET = """
export interface User { id: number; name: string }

export class UserService {
  async fetchUser(id: number): Promise<User> {
    return this.request(`/api/user/${id}`);
  }
  private request(path: string) { return fetch(path); }
}

export function makeService(): UserService { return new UserService(); }
const helper = (x: number) => x + 1;
const notAFunction = 42;
type Id = string;
enum Kind { A, B }
"""


def _pack_available() -> bool:
    try:
        import tree_sitter_language_pack  # noqa: F401
    except ImportError:
        return False
    return True


requires_pack = pytest.mark.skipif(
    not _pack_available(), reason="tree-sitter-language-pack (opt-in extra) not installed"
)


class TestLanguageMapping:
    def test_maps_programming_languages(self):
        assert treesitter_symbols.language_for_path("src/app.ts") == "typescript"
        assert treesitter_symbols.language_for_path("src/app.tsx") == "tsx"
        assert treesitter_symbols.language_for_path("src/app.mjs") == "javascript"
        assert treesitter_symbols.language_for_path("cmd/main.go") == "go"
        assert treesitter_symbols.language_for_path("src/lib.rs") == "rust"

    def test_ignores_python_and_non_code(self):
        """Python has its own ast path; data formats carry no declarations."""
        assert treesitter_symbols.language_for_path("mod.py") is None
        assert treesitter_symbols.language_for_path("package.json") is None
        assert treesitter_symbols.language_for_path("config.yaml") is None
        assert treesitter_symbols.language_for_path("Makefile") is None

    def test_extension_match_is_case_insensitive(self):
        assert treesitter_symbols.language_for_path("SRC/App.TS") == "typescript"


class TestGracefulDegradation:
    """Absence of the grammar pack must degrade coverage, never correctness."""

    def test_extract_returns_zero_without_pack(self, monkeypatch):
        monkeypatch.setattr(treesitter_symbols, "_load_pack", lambda: None)
        graph = CodeSymbolGraph()

        added = treesitter_symbols.extract_symbols("a.ts", "function f() {}", graph, {})

        assert added == 0
        assert graph.symbols == {}

    def test_scan_still_indexes_python_without_pack(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(treesitter_symbols, "_load_pack", lambda: None)
        (tmp_path / "svc.py").write_text("def worker():\n    return 1\n", encoding="utf-8")
        (tmp_path / "app.ts").write_text("export function ignored() {}\n", encoding="utf-8")

        graph = CodeSymbolGraph()
        count = graph.scan_directory(str(tmp_path))

        assert count >= 1
        assert any(s.name == "worker" for s in graph.symbols.values())
        assert not any(s.name == "ignored" for s in graph.symbols.values())
        # A failed scan is reported, not silently absorbed.
        assert graph.last_scan_stats.get("languages") == set()

    def test_unavailable_reason_is_a_non_empty_string(self, monkeypatch):
        monkeypatch.setattr(treesitter_symbols, "_load_pack", lambda: None)
        monkeypatch.setattr(treesitter_symbols, "_PACK_ERROR", "simulated absence")

        assert treesitter_symbols.available() is False
        assert treesitter_symbols.unavailable_reason() == "simulated absence"


@requires_pack
class TestTypeScriptExtraction:
    def _graph(self):
        graph = CodeSymbolGraph()
        stats: dict = {"languages": set(), "calls": 0}
        added = treesitter_symbols.extract_symbols("src/user.ts", TS_SNIPPET, graph, stats)
        return graph, stats, added

    def test_extracts_declarations(self):
        graph, _, added = self._graph()
        assert added == 8

        kinds = {s.name: s.kind for s in graph.symbols.values()}
        assert kinds["User"] == "interface"
        assert kinds["UserService"] == "class"
        assert kinds["makeService"] == "function"
        assert kinds["Id"] == "type"
        assert kinds["Kind"] == "enum"
        # The whole declaration surface is captured — no silent omissions.
        assert set(kinds) == {
            "User", "UserService", "fetchUser", "request",
            "makeService", "helper", "Id", "Kind",
        }

    def test_nests_methods_under_their_class(self):
        graph, _, _ = self._graph()
        methods = {s.name: s for s in graph.symbols.values() if s.kind == "method"}
        assert set(methods) == {"fetchUser", "request"}
        assert methods["fetchUser"].container_name == "UserService"
        assert methods["fetchUser"].qualified_name == "UserService.fetchUser"

    def test_records_call_edges(self):
        graph, _, _ = self._graph()
        # Bare-name lookup mirrors the Python path so callers resolve by name.
        assert "fetchUser" in graph.get_callers("request")
        assert "request" in graph.get_callers("fetch")

    def test_arrow_function_binding_is_a_function(self):
        """Arrow functions are the dominant TS/JS form — a declaration-only walk misses them."""
        graph, _, _ = self._graph()
        kinds = {s.name: s.kind for s in graph.symbols.values()}
        assert kinds.get("helper") == "function"
        assert "notAFunction" not in kinds

    def test_reports_parse_errors_without_losing_symbols(self):
        """Recovery: a file the grammar flags still yields what it could parse."""
        graph = CodeSymbolGraph()
        stats: dict = {"languages": set()}
        added = treesitter_symbols.extract_symbols(
            "bad.tsx", "export const A = () => <p>a & b</p>;\nexport function ok() {}\n", graph, stats
        )
        assert added >= 1
        assert any(s.name == "ok" for s in graph.symbols.values())
        assert stats.get("parse_errors", 0) >= 1

    def test_unknown_language_extension_is_ignored(self):
        graph = CodeSymbolGraph()
        assert treesitter_symbols.extract_symbols("notes.txt", "hello", graph, {}) == 0
        assert graph.symbols == {}


@requires_pack
def test_scan_directory_indexes_typescript(tmp_path: Path):
    """Integration: a .ts file reaches the graph through scan_directory, not just directly."""
    (tmp_path / "svc.py").write_text("def py_fn():\n    return 1\n", encoding="utf-8")
    (tmp_path / "app.ts").write_text(
        "export function tsFn() { return helper(); }\nfunction helper() { return 2; }\n",
        encoding="utf-8",
    )

    graph = CodeSymbolGraph()
    graph.scan_directory(str(tmp_path))

    names = {s.name for s in graph.symbols.values()}
    assert {"py_fn", "tsFn", "helper"} <= names
    assert graph.last_scan_stats.get("languages") == {"typescript"}
    assert "app.ts" in graph.file_symbols
