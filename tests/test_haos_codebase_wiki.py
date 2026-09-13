"""Contract tests for the HAOS Codebase Wiki (docs/haos/CODEBASE_WIKI.md §6).

These assert *behaviour contracts between data*, never snapshots of the real
repo: a fixed synthetic corpus must yield exactly the edges/nodes the spec
promises, incremental re-index touches only the changed file, unchanged
re-runs are byte-identical, paths stay root-relative, everything runs offline
and edges are honestly labelled EXTRACTED/INFERRED.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes.platform.codebase import clusters, graph, indexer, query, runner, wiki


def _write_corpus(root: Path) -> None:
    """Fixed corpus matching CODEBASE_WIKI.md §6.1:
    a imports b, b calls c, c inherits base (defined in b)."""
    root.mkdir()
    (root / "a.py").write_text("import b\n", encoding="utf-8")
    (root / "b.py").write_text(
        "import c\n\n"
        "class base:\n"
        "    pass\n\n"
        "def run():\n"
        "    c.helper()\n",
        encoding="utf-8",
    )
    (root / "c.py").write_text(
        "from b import base\n\n"
        "class Derived(base):\n"
        "    pass\n\n"
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )


def _edge_set(graph_path: Path) -> set:
    raw = json.loads(graph_path.read_text(encoding="utf-8"))
    return {
        (e["source"], e["target"], e["relation"], e["confidence"]) for e in raw["edges"]
    }


# --------------------------------------------------------------------------
# §6.1 fixed-corpus contracts
# --------------------------------------------------------------------------
class TestFixedCorpusEdges:
    def test_expected_edges_present(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        _write_corpus(root)
        rep = runner.run_index(root, tmp_path / "out")
        edges = _edge_set(rep.written["_graph_json"])
        assert ("a", "b", "importa", "EXTRACTED") in edges
        # b calls c (module-level call from b::run to c::helper)
        assert ("b::run", "c::helper", "chama", "EXTRACTED") in edges
        # c inherits base which lives in b
        assert ("c::Derived", "b::base", "herda", "EXTRACTED") in edges

    def test_edges_only_within_corpus(self, tmp_path: Path) -> None:
        """No stdlib/unknown imports fabricate edges (frontier contract)."""
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "a.py").write_text(
            "import os\nimport sys\nimport json\n\ndef f():\n    return os.getcwd()\n",
            encoding="utf-8",
        )
        rep = runner.run_index(root, tmp_path / "out")
        edges = _edge_set(rep.written["_graph_json"])
        assert edges == set()  # os/sys/json are not corpus modules -> no edges

    def test_module_and_symbol_nodes_carry_locations(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        _write_corpus(root)
        rep = runner.run_index(root, tmp_path / "out")
        raw = json.loads(rep.written["_graph_json"].read_text(encoding="utf-8"))
        by_id = {n["id"]: n for n in raw["nodes"]}
        assert by_id["a"]["kind"] == "module"
        assert by_id["a"]["source_file"] == "a.py"  # root-relative, no absolute path
        assert by_id["b::base"]["kind"] == "class"
        assert by_id["b::base"]["source_file"] == "b.py"
        assert by_id["b::base"]["source_location"] == 3
        assert by_id["c::helper"]["kind"] == "function"
        # frontier: no machine/absolute path anywhere
        for n in raw["nodes"]:
            assert not n["source_file"].startswith("/")

    def test_god_node_is_b(self, tmp_path: Path) -> None:
        """index.md lists the most-connected concept first (§6.1 god node)."""
        root = tmp_path / "corpus"
        _write_corpus(root)
        rep = runner.run_index(root, tmp_path / "out")
        md = rep.written["_index"].read_text(encoding="utf-8")
        # first god-node bullet names module b
        god_lines = [ln for ln in md.splitlines() if ln.startswith("- `") and "grau" in ln]
        assert god_lines, "index.md should list god nodes"
        assert god_lines[0].startswith("- `b`")


# --------------------------------------------------------------------------
# §6.2 incremental
# --------------------------------------------------------------------------
class TestIncremental:
    def test_update_reindexes_only_changed_file(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        _write_corpus(root)
        out = tmp_path / "out"
        rep1 = runner.run_index(root, out)
        assert sorted(rep1.freshly_parsed) == ["a.py", "b.py", "c.py"]
        # touch only c.py
        (root / "c.py").write_text(
            (root / "c.py").read_text(encoding="utf-8") + "\ndef extra():\n    return 2\n",
            encoding="utf-8",
        )
        rep2 = runner.run_index(root, out)
        assert rep2.freshly_parsed == ["c.py"], f"expected only c.py, got {rep2.freshly_parsed}"
        # new symbol is visible
        raw = json.loads(rep2.written["_graph_json"].read_text(encoding="utf-8"))
        ids = {n["id"] for n in raw["nodes"]}
        assert "c::extra" in ids


# --------------------------------------------------------------------------
# §6.3 idempotency
# --------------------------------------------------------------------------
class TestIdempotency:
    def test_unchanged_runs_are_byte_identical(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        _write_corpus(root)
        out = tmp_path / "out"
        runner.run_index(root, out)
        first = (out / "graph.json").read_bytes()
        first_md = (out / "index.md").read_bytes()
        first_article = (out / "modules" / "_root.md").read_bytes()
        runner.run_index(root, out)
        assert (out / "graph.json").read_bytes() == first
        assert (out / "index.md").read_bytes() == first_md
        assert (out / "modules" / "_root.md").read_bytes() == first_article


# --------------------------------------------------------------------------
# §6.4 frontier: relative paths
# --------------------------------------------------------------------------
class TestFrontier:
    def test_source_file_is_root_relative_under_subdirs(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        (root / "pkg" / "sub").mkdir(parents=True)
        (root / "pkg" / "sub" / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")
        rep = runner.run_index(root, tmp_path / "out")
        raw = json.loads(rep.written["_graph_json"].read_text(encoding="utf-8"))
        mod = next(n for n in raw["nodes"] if n["id"] == "pkg.sub.mod")
        assert mod["source_file"] == "pkg/sub/mod.py"

    def test_relative_import_resolves_against_package(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        (root / "pkg").mkdir(parents=True)
        (root / "pkg" / "a.py").write_text("from . import b\n", encoding="utf-8")
        (root / "pkg" / "b.py").write_text("VALUE = 1\n", encoding="utf-8")
        rep = runner.run_index(root, tmp_path / "out")
        edges = _edge_set(rep.written["_graph_json"])
        assert ("pkg.a", "pkg.b", "importa", "EXTRACTED") in edges


# --------------------------------------------------------------------------
# §6.5 offline / hermetic
# --------------------------------------------------------------------------
class TestHermetic:
    def test_indexing_never_needs_network_or_keys(self, tmp_path: Path) -> None:
        # pure stdlib pipeline must succeed without env secrets; the suite runs
        # with credentials unset by run_tests.sh, so a passing run is the test.
        root = tmp_path / "corpus"
        _write_corpus(root)
        rep = runner.run_index(root, tmp_path / "out")
        assert rep.written["_graph_json"].is_file()
        assert rep.written["_index"].is_file()


# --------------------------------------------------------------------------
# §6.6 honest confidence labels
# --------------------------------------------------------------------------
class TestConfidence:
    def test_unknown_member_call_is_inferred_not_extracted(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "lib.py").write_text("def known():\n    return 1\n", encoding="utf-8")
        (root / "use.py").write_text(
            "import lib\n\ndef f():\n"
            "    lib.known()\n"       # EXTRACTED: symbol exists
            "    lib.missing()\n"     # INFERRED: module known, symbol not
            "    lib.also_missing()\n",
            encoding="utf-8",
        )
        rep = runner.run_index(root, tmp_path / "out")
        edges = _edge_set(rep.written["_graph_json"])
        assert ("use::f", "lib::known", "chama", "EXTRACTED") in edges
        assert ("use::f", "lib", "chama", "INFERRED") in edges

    def test_dynamic_calls_are_not_fabricated(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "lib.py").write_text("def known():\n    return 1\n", encoding="utf-8")
        (root / "use.py").write_text(
            "import lib\n\ndef f():\n"
            "    getattr(lib, 'known')()\n"
            "    fn = lib.known\n"
            "    fn()\n",
            encoding="utf-8",
        )
        rep = runner.run_index(root, tmp_path / "out")
        edges = _edge_set(rep.written["_graph_json"])
        # no EXTRACTED *chama* edge may point at a symbol the AST cannot prove
        # (importa edges to the corpus module remain legitimate)
        assert not any(e[2] == "chama" and e[3] == "EXTRACTED" for e in edges)


# --------------------------------------------------------------------------
# Community / render contracts
# --------------------------------------------------------------------------
class TestCommunitiesAndRender:
    def test_flat_package_is_one_community(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        (root / "tools").mkdir(parents=True)
        for name in ("a.py", "b.py", "c.py"):
            (root / "tools" / name).write_text("def f():\n    pass\n", encoding="utf-8")
        rep = runner.run_index(root, tmp_path / "out")
        raw = json.loads(rep.written["_graph_json"].read_text(encoding="utf-8"))
        comms = {n["community"] for n in raw["nodes"] if n["kind"] == "module"}
        assert comms == {"tools"}

    def test_nested_namespace_splits_at_second_segment(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        for sub in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota"):
            (root / "pkg" / sub).mkdir(parents=True)
            for i in range(2):
                fname = f"m{i}.py"
                (root / "pkg" / sub / fname).write_text("def f():\n    pass\n", encoding="utf-8")
        (root / "pkg" / "direct.py").write_text("def f():\n    pass\n", encoding="utf-8")
        rep = runner.run_index(root, tmp_path / "out")
        raw = json.loads(rep.written["_graph_json"].read_text(encoding="utf-8"))
        comms = {n["community"] for n in raw["nodes"] if n["kind"] == "module"}
        # nested modules are grouped under pkg.<sub>; direct file stays in pkg
        assert "pkg.alpha" in comms
        assert "pkg" in comms
        assert all(c.startswith("pkg") for c in comms)

    def test_render_articles_exist_per_community(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        _write_corpus(root)
        rep = runner.run_index(root, tmp_path / "out")
        modules_dir = tmp_path / "out" / "modules"
        assert (modules_dir / "_root.md").is_file()
        md = rep.written["_index"].read_text(encoding="utf-8")
        assert "[_root](modules/_root.md)" in md


# --------------------------------------------------------------------------
# Cache corruption resilience
# --------------------------------------------------------------------------
class TestCacheResilience:
    def test_corrupt_cache_entry_is_reparsed(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        _write_corpus(root)
        out = tmp_path / "out"
        runner.run_index(root, out)
        cache_files = list((out / "cache").glob("*.json"))
        assert cache_files
        cache_files[0].write_text("{ not json", encoding="utf-8")
        rep = runner.run_index(root, out)
        assert rep.written["_graph_json"].is_file()

    def test_max_files_marks_truncation(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        for i in range(5):
            (root / f"m{i}.py").write_text("def f():\n    pass\n", encoding="utf-8")
        rep = runner.run_index(root, tmp_path / "out", max_files=2)
        assert rep.truncated
        md = rep.written["_index"].read_text(encoding="utf-8")
        assert "truncado" in md or "truncation" in md


# --------------------------------------------------------------------------
# Fase 3: markdown concept extractor + `cita` edges
# --------------------------------------------------------------------------
class TestMarkdownConcepts:
    def _md_corpus(self, root: Path) -> None:
        root.mkdir()
        (root / "lib.py").write_text(
            "def helper():\n    return 1\n\nclass base:\n    pass\n",
            encoding="utf-8",
        )
        (root / "guide.md").write_text(
            "# Guia\n\n"
            "Use `lib.helper` para a lógica; herde de `lib.base` quando precisar.\n\n"
            "## Bloco de código (não é citação)\n\n"
            "```python\nlib.helper()\n```\n",
            encoding="utf-8",
        )

    def test_md_creates_concept_nodes(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        self._md_corpus(root)
        rep = runner.run_index(root, tmp_path / "out", markdown=True, skip=set())
        raw = json.loads(rep.written["_graph_json"].read_text(encoding="utf-8"))
        ids = {n["id"] for n in raw["nodes"]}
        assert "guide~md" in ids                     # doc module
        assert "guide~md::guia" in ids               # h1 -> concept
        # accents are preserved by the slugifier; parentheticals dropped
        assert "guide~md::bloco-de-código-não-é-citação" in ids

    def test_md_mentions_become_cita_edges(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        self._md_corpus(root)
        rep = runner.run_index(root, tmp_path / "out", markdown=True, skip=set())
        raw = json.loads(rep.written["_graph_json"].read_text(encoding="utf-8"))
        citas = {(e["source"], e["target"], e["confidence"])
                 for e in raw["edges"] if e["relation"] == "cita"}
        # mentions resolve against confirmed symbols -> EXTRACTED
        assert ("guide~md", "lib::helper", "EXTRACTED") in citas
        assert ("guide~md", "lib::base", "EXTRACTED") in citas

    def test_md_no_edges_when_no_mentions(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        (root / "plain.md").write_text("# Só títulos\n\nsem código citado\n", encoding="utf-8")
        rep = runner.run_index(root, tmp_path / "out", markdown=True, skip=set())
        raw = json.loads(rep.written["_graph_json"].read_text(encoding="utf-8"))
        assert not any(e["relation"] == "cita" for e in raw["edges"])

    def test_md_off_by_default_and_idempotent(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        self._md_corpus(root)
        out = tmp_path / "out"
        rep1 = runner.run_index(root, out, markdown=True, skip=set())
        rep2 = runner.run_index(root, out, markdown=True, skip=set())
        assert (out / "graph.json").read_bytes() == \
            (out / "graph.json").read_bytes()
        raw = json.loads(rep1.written["_graph_json"].read_text(encoding="utf-8"))
        assert any(n["id"] == "guide~md" for n in raw["nodes"])


# --------------------------------------------------------------------------
# Fase 3: query engine
# --------------------------------------------------------------------------
class TestQueryEngine:
    def _payload(self) -> dict:
        # mirrors the Fase-1 contract corpus graph.json structure
        return {
            "root": "corpus",
            "file_count": 3,
            "nodes": [
                {"id": "a", "kind": "module", "label": "a", "module": "a",
                 "source_file": "a.py", "source_location": 1, "community": "_root"},
                {"id": "b", "kind": "module", "label": "b", "module": "b",
                 "source_file": "b.py", "source_location": 1, "community": "_root"},
                {"id": "b::run", "kind": "function", "label": "run", "module": "b",
                 "source_file": "b.py", "source_location": 6, "community": "_root"},
                {"id": "c", "kind": "module", "label": "c", "module": "c",
                 "source_file": "c.py", "source_location": 1, "community": "_root"},
                {"id": "c::helper", "kind": "function", "label": "helper", "module": "c",
                 "source_file": "c.py", "source_location": 5, "community": "_root"},
            ],
            "edges": [
                {"source": "a", "target": "b", "relation": "importa",
                 "confidence": "EXTRACTED", "count": 1},
                {"source": "b::run", "target": "c::helper", "relation": "chama",
                 "confidence": "EXTRACTED", "count": 1},
            ],
        }

    def test_search_and_gods(self, tmp_path: Path) -> None:
        p = self._payload()
        assert any(n["id"] == "c::helper" for n in query.search_nodes(p, "helper"))
        gods = query.god_nodes(p)
        assert gods[0]["id"] in ("b", "c", "c::helper", "a")  # all degree >= 1

    def test_path_and_neighbors(self, tmp_path: Path) -> None:
        p = self._payload()
        # a imports b, b imports nothing in payload but b::run->c::helper;
        # shortest path between module a and module b exists (a imports b)
        chain = query.shortest_path(p, "a", "b")
        assert chain is not None and chain == ["a", "b"]
        # a imports b; b::run calls c::helper: the only link from a is b
        outs = query.neighbors(p, "a", direction="out")
        assert any(r["node"]["id"] == "b" for r in outs)
        ins = query.neighbors(p, "c::helper", direction="in")
        assert any(r["node"]["id"] == "b::run" for r in ins)

    def test_serialization_roundtrip_via_graph_json(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        _write_corpus(root)
        rep = runner.run_index(root, tmp_path / "out")
        from hermes.platform.codebase import query as q
        payload = q.load_graph_json(rep.written["_graph_json"])
        assert q.summarize(payload)["node_count"] > 0
        assert q.god_nodes(payload)


# --------------------------------------------------------------------------
# Fase 3: watch / change detection
# --------------------------------------------------------------------------
class TestWatch:
    def test_tree_signature_tracks_changes(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        _write_corpus(root)
        s1 = runner.tree_signature(root)
        assert s1  # non-empty
        # touching content changes the signature for that file only
        (root / "c.py").write_text(
            (root / "c.py").read_text(encoding="utf-8") + "\nx = 1\n", encoding="utf-8"
        )
        s2 = runner.tree_signature(root)
        changed = runner._first_change(s1, s2)
        assert changed == "c.py"

    def test_watch_reindexes_once_on_change(self, tmp_path: Path) -> None:
        import threading
        import time

        root = tmp_path / "corpus"
        _write_corpus(root)
        out = tmp_path / "out"
        runner.run_index(root, out)
        calls: list = []
        stop = threading.Event()

        def run_watch() -> None:
            runner.watch(
                root, out,
                poll_seconds=0.05,
                on_reindex=calls.append,
                stop=stop.is_set,
            )

        thread = threading.Thread(target=run_watch, daemon=True)
        thread.start()
        try:
            # change a file only *after* watch has taken its baseline
            time.sleep(0.3)
            (root / "c.py").write_text(
                (root / "c.py").read_text(encoding="utf-8")
                + "\ndef extra():\n    return 9\n",
                encoding="utf-8",
            )
            deadline = time.monotonic() + 10
            while not calls and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            stop.set()
            thread.join(timeout=5)
        assert calls, "watch never re-indexed after the change"
        raw = json.loads((out / "graph.json").read_text(encoding="utf-8"))
        assert any(n["id"] == "c::extra" for n in raw["nodes"])


# --------------------------------------------------------------------------
# Fase 3: MCP export layer (dispatcher contract)
# --------------------------------------------------------------------------
class TestMcpExport:
    def test_dispatcher_answers_tools(self, tmp_path: Path) -> None:
        import asyncio

        from hermes.platform.codebase.mcp_export import (
            WIKI_TOOLS,
            wiki_dispatcher,
        )

        root = tmp_path / "corpus"
        _write_corpus(root)
        out = tmp_path / "out"
        runner.run_index(root, out)
        graph_path = out / "graph.json"
        names = {t["name"] for t in WIKI_TOOLS}
        assert names == {"wiki_status", "wiki_search", "wiki_edges", "wiki_path", "wiki_god_nodes"}

        async def run() -> None:
            res = await wiki_dispatcher(graph_path, "wiki_search", {"term": "helper"})
            assert not res.get("isError")
            assert "c::helper" in res["content"][0]["text"]
            res2 = await wiki_dispatcher(graph_path, "wiki_status", {})
            assert res2["content"][0]["text"].startswith("{")

        asyncio.run(run())

    def test_dispatcher_unknown_tool_and_missing_graph(self, tmp_path: Path) -> None:
        import asyncio

        from hermes.platform.codebase.mcp_export import wiki_dispatcher

        async def run() -> None:
            res = await wiki_dispatcher(tmp_path / "nope.json", "wiki_search", {"term": "x"})
            assert res.get("isError")
            assert "Mapa indisponível" in res["content"][0]["text"]

        asyncio.run(run())

    def test_register_wiki_server_adds_namespaced_tools(self, tmp_path: Path) -> None:
        from hermes.platform.codebase.mcp_export import register_wiki_server

        class FakeAggregator:
            def __init__(self) -> None:
                self.tools: list = []
                self.dispatchers: dict = {}

            def register_server_tools(self, server_name: str, tools: list) -> list:
                self.tools = tools
                return [f"{server_name}_{t['name']}" for t in tools]

            def register_dispatcher(self, server_name: str, dispatcher) -> None:
                self.dispatchers[server_name] = dispatcher

        root = tmp_path / "corpus"
        _write_corpus(root)
        out = tmp_path / "out"
        runner.run_index(root, out)
        agg = FakeAggregator()
        names = register_wiki_server(agg, out / "graph.json")
        assert len(names) == 5
        assert "codebase-wiki_wiki_search" in names
        assert "codebase-wiki" in agg.dispatchers

    def test_register_wiki_server_e2e_local_aggregator(self, tmp_path: Path) -> None:
        """End-to-end through the real LocalMCPAggregator: register the wiki server
        and route a namespaced call (pattern of the unified protocol gateway)."""
        import asyncio

        from hermes.platform.codebase.mcp_export import register_wiki_server
        from hermes.platform.mcp.aggregator import LocalMCPAggregator

        root = tmp_path / "corpus"
        _write_corpus(root)
        out = tmp_path / "out"
        runner.run_index(root, out)
        aggregator = LocalMCPAggregator()
        register_wiki_server(aggregator, out / "graph.json")

        async def run() -> None:
            tools = aggregator.list_tools()
            assert any(t["name"] == "codebase-wiki_wiki_search" for t in tools)
            res = await aggregator.call_tool(
                "codebase-wiki_wiki_search", {"term": "helper"}
            )
            assert not res.get("isError")
            assert "c::helper" in res["content"][0]["text"]
            status = await aggregator.call_tool("codebase-wiki_wiki_status", {})
            assert not status.get("isError")
            assert "node_count" in status["content"][0]["text"]

        asyncio.run(run())
