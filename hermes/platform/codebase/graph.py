"""Corpus-wide graph merge for the HAOS Codebase Wiki.

Pass 2 of the pipeline: take the per-file ``FileFacts`` produced by
``indexer.scan_facts`` and resolve them into a single directed graph:

* nodes  — one per module plus one per top-level symbol (``module::symbol``),
  each carrying its ``source_file`` (root-relative) and line;
* edges  — ``importa`` (module imports module), ``chama`` (a symbol calls
  another symbol/module) and ``herda`` (a class inherits another class), all
  tagged ``EXTRACTED`` when the target is confirmed by a real symbol/module
  and ``INFERRED`` when only the leading module is known;

Confidence follows the graphify convention: an edge is ``EXTRACTED`` only when
its target node provably exists in the corpus; a call whose target symbol
cannot be confirmed (dynamic dispatch, dotted member of an imported module
that has no such symbol) degrades to ``INFERRED`` against the module the
leading name resolves to. Bare calls to stdlib/unknown names resolve to
nothing and are dropped — the wiki is honest about what it cannot see.

Plain dicts + pure functions; no side effects and no third-party imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .indexer import FileFacts, SKIP_DIRS

EDGE_IMPORTA = "importa"
EDGE_CHAMA = "chama"
EDGE_HERDA = "herda"
EDGE_CITA = "cita"          # doc mentions a code symbol (markdown extractor)
CONF_EXTRACTED = "EXTRACTED"
CONF_INFERRED = "INFERRED"

MODULE_SEP = "::"


# --------------------------------------------------------------------------
# Import resolution
# --------------------------------------------------------------------------
def _absolute_dotted(level: int, current_module: str, target: str) -> str:
    """Resolve a possibly-relative dotted import against the importing module.

    Hermes uses namespace packages, so the module id IS the dotted path; a
    relative import ``from .foo import x`` inside ``a.b.c`` resolves to
    ``a.b.foo`` (drop ``level`` leading segments from the importing module,
    then append ``target``). ``from . import x`` has an empty target.
    """
    parts = current_module.split(".")
    if level == 0:
        return target
    if level > len(parts):
        return ""
    base = parts[:-level]
    if target:
        return ".".join(base + [target])
    return ".".join(base)


def _import_bindings(
    facts: FileFacts,
    module_id: str,
    known: Set[str],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Split a module's imports into (module_binds, symbol_binds).

    * ``import x.y`` binds ``x`` (or the asname) to module ``x.y``;
    * ``from pkg import sub`` binds ``sub`` to ``pkg.sub`` WHEN a module
      ``pkg.sub`` exists in the corpus (submodule import), else to the symbol
      ``pkg::sub`` via ``symbol_binds`` (when ``pkg`` itself is a module, e.g.
      a package with ``__init__``);
    * ``from . import sub`` inside ``pkg/a`` binds ``sub`` to ``pkg.sub`` when
      that module exists (the namespace-package case — no ``__init__``);
    * ``from .x import y`` binds ``y`` to the module ``<base>.x``.

    ``module_binds`` feed dotted-path resolution (calls like ``sub.fn()``),
    ``symbol_binds`` feed direct-symbol resolution (``fn()`` after a
    ``from ... import fn``).
    """
    mod_binds: Dict[str, str] = {}
    sym_binds: Dict[str, str] = {}
    for imp in facts.imports:
        if imp.mode == "import":
            bound = imp.alias or imp.target.split(".")[0]
            mod_binds[bound] = imp.target
            continue
        # from-import: anchor is the package/module the names come from
        anchor = _absolute_dotted(imp.level, module_id, imp.target)
        if not imp.target:
            # ``from . import x`` — x names sibling submodules (namespace
            # packages have no __init__ to hold symbols).
            for name in imp.names:
                sub = f"{anchor}.{name}" if anchor else name
                if sub in known:
                    mod_binds[name] = sub
                elif anchor in known:
                    sym_binds[name] = anchor
            continue
        if anchor not in known:
            continue
        for name in imp.names:
            sub = f"{anchor}.{name}"
            if sub in known:
                mod_binds[name] = sub
            else:
                sym_binds[name] = anchor
    return mod_binds, sym_binds


def _import_edges_for(facts: FileFacts, module_id: str, known: Set[str]) -> List[GraphEdge]:
    """Module-level ``importa`` edges produced by one file's imports."""
    edges: List[GraphEdge] = []
    seen: Set[Tuple[str, str]] = set()
    for imp in facts.imports:
        if imp.mode == "import":
            if imp.target in known and imp.target != module_id:
                seen.add((module_id, imp.target))
            continue
        # from-import: target module is the base for the whole statement
        anchor = _absolute_dotted(imp.level, module_id, imp.target)
        if not imp.target:
            # ``from . import x`` — x is a sibling submodule in namespace pkgs
            for name in imp.names:
                sub = _absolute_dotted(imp.level, module_id, name)
                if sub in known and sub != module_id:
                    seen.add((module_id, sub))
            continue
        if anchor in known and anchor != module_id:
            seen.add((module_id, anchor))
    for src, tgt in sorted(seen):
        edges.append(
            GraphEdge(source=src, target=tgt, relation=EDGE_IMPORTA, confidence=CONF_EXTRACTED)
        )
    return edges


# --------------------------------------------------------------------------
# Graph model
# --------------------------------------------------------------------------
@dataclass
class GraphNode:
    id: str
    kind: str                      # "module" | "class" | "function" | "async_function"
    label: str
    module: str
    source_file: str               # root-relative, forward slashes
    source_location: int
    members: List[str] = field(default_factory=list)


@dataclass
class GraphEdge:
    source: str
    target: str
    relation: str
    confidence: str
    count: int = 1


@dataclass
class CodeGraph:
    root: str
    nodes: Dict[str, GraphNode] = field(default_factory=dict)
    edges: List[GraphEdge] = field(default_factory=list)
    truncated: bool = False
    file_count: int = 0

    def module_ids(self) -> Set[str]:
        return {n.module for n in self.nodes.values() if n.kind == "module"}

    def symbol_exists(self, symbol_id: str) -> bool:
        return symbol_id in self.nodes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "truncated": self.truncated,
            "file_count": self.file_count,
            "nodes": [self._node_dict(n) for n in sorted(self.nodes.values(), key=lambda n: n.id)],
            "edges": sorted(
                (
                    {
                        "source": e.source,
                        "target": e.target,
                        "relation": e.relation,
                        "confidence": e.confidence,
                        "count": e.count,
                    }
                    for e in self.edges
                ),
                key=lambda e: (e["source"], e["target"], e["relation"], e["confidence"]),
            ),
        }

    @staticmethod
    def _node_dict(n: GraphNode) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": n.id,
            "kind": n.kind,
            "label": n.label,
            "module": n.module,
            "source_file": n.source_file,
            "source_location": n.source_location,
        }
        if n.members:
            d["members"] = n.members
        return d


def rel_to_source_file(rel_str: str) -> str:
    return rel_str.replace("\\", "/")


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
def _relpath_for_module(module_id: str) -> str:
    """Best-effort root-relative source path for a module id (``a.b`` -> ``a/b.py``).

    Called only when facts carry no explicit path (module-level nodes mirror
    their file facts, which always know the real path, so this fallback is
    rarely exercised).
    """
    if not module_id:
        return ""
    return module_id.replace(".", "/") + ".py"


def build_graph(
    facts_by_module: Dict[str, FileFacts],
    root: str = ".",
    *,
    rel_paths: Optional[Dict[str, str]] = None,
    max_files: Optional[int] = None,
) -> CodeGraph:
    """Merge file facts into a directed code graph.

    ``rel_paths`` maps module_id -> root-relative path when available so
    ``source_file`` is exact; otherwise the dotted module id is converted back
    to a path. ``max_files`` caps the number of modules (deterministic: module
    ids sorted) so a giant corpus can still be indexed with an explicit,
    visible truncation marker.
    """
    graph = CodeGraph(root=root, file_count=len(facts_by_module))
    modules = sorted(facts_by_module.keys())
    if max_files is not None and len(modules) > max_files:
        modules = modules[:max_files]
        graph.truncated = True

    rel_paths = rel_paths or {}

    # Pass A: nodes — one module node per module, plus one node per symbol.
    for module_id in modules:
        facts = facts_by_module[module_id]
        src = rel_to_source_file(rel_paths.get(module_id, _relpath_for_module(module_id)))
        label = module_id.rsplit(".", 1)[-1] if "." in module_id else module_id
        graph.nodes[module_id] = GraphNode(
            id=module_id, kind="module", label=label or module_id,
            module=module_id, source_file=src, source_location=1,
        )
        for name, sym in facts.symbols.items():
            symbol_id = f"{module_id}{MODULE_SEP}{name}"
            graph.nodes[symbol_id] = GraphNode(
                id=symbol_id,
                kind=sym.kind,
                label=name,
                module=module_id,
                source_file=src,
                source_location=sym.lineno,
                members=list(sym.members),
            )

    # Pass B: edges.
    _add_import_edges(graph, facts_by_module, modules)
    _add_call_edges(graph, facts_by_module, modules)
    _add_herda_edges(graph, facts_by_module, modules)
    _add_cita_edges(graph, facts_by_module, modules)
    return graph


def _add_cita_edges(graph: CodeGraph, facts_by_module, modules) -> None:
    """Doc->code ``cita`` edges from markdown mention lists (Fase 3).

    A mention is a dotted id in the prose of a ``.md`` file. It resolves the
    same way a call path does — longest known module prefix; the remaining
    segments name a symbol — and becomes ``cita`` (EXTRACTED when the exact
    symbol exists, INFERRED when only its module is confirmed). Mentions that
    name nothing in the corpus are dropped: docs citing stdlib or unknown ids
    add no edges.
    """
    known = set(modules)
    for module_id in modules:
        facts = facts_by_module[module_id]
        if not facts.mentions:
            continue
        for mention in facts.mentions:
            resolved = _resolve_mention(mention, known=known, graph=graph)
            if resolved is None:
                continue
            target, confidence = resolved
            if target == module_id:
                continue
            graph.edges.append(
                GraphEdge(
                    source=module_id, target=target,
                    relation=EDGE_CITA, confidence=confidence,
                )
            )


def _resolve_mention(
    dotted: str,
    *,
    known: Set[str],
    graph: CodeGraph,
) -> Optional[Tuple[str, str]]:
    """Resolve a prose dotted id to a module/symbol node id + confidence."""
    csegs = dotted.split(".")
    for i in range(len(csegs), 0, -1):
        prefix = ".".join(csegs[:i])
        if prefix not in known:
            continue
        rest = csegs[i:]
        if not rest:
            return prefix, CONF_INFERRED  # module named, nothing more claimed
        symbol_id = f"{prefix}{MODULE_SEP}{'.'.join(rest)}"
        if graph.symbol_exists(symbol_id):
            return symbol_id, CONF_EXTRACTED
        return prefix, CONF_INFERRED
    return None


def _add_import_edges(graph: CodeGraph, facts_by_module, modules) -> None:
    """module A imports module B (EXTRACTED when B is a corpus module)."""
    known = set(modules)
    for module_id in modules:
        facts = facts_by_module[module_id]
        graph.edges.extend(_import_edges_for(facts, module_id, known))


def _resolve_target(
    path: str,
    *,
    module_id: str,
    facts: FileFacts,
    known: Set[str],
    mod_imports: Dict[str, str],
    from_syms: Dict[str, str],
    graph: CodeGraph,
) -> Optional[Tuple[str, str]]:
    """Resolve a dotted path to (target_node_id, confidence) or None.

    Tries, in order:
    1. a from-imported symbol called directly (``dispatch(...)`` where
       ``from tools.registry import dispatch``) -> the symbol node;
    2. a module imported with a prefix match over the path — the LONGEST known
       module that prefixes the path wins, and the remaining segments name the
       symbol (``tools.registry.dispatch`` -> symbol ``tools.registry::dispatch``);
    3. the importing module's own symbols (bare local names).
    A confirmed symbol yields ``EXTRACTED``; a confirmed module whose member
    cannot be verified yields ``INFERRED`` against that module.
    """
    segs = path.split(".")
    # 1. from-imported symbol called directly.
    if len(segs) == 1 and segs[0] in from_syms:
        m = from_syms[segs[0]]
        symbol_id = f"{m}{MODULE_SEP}{segs[0]}"
        if graph.symbol_exists(symbol_id):
            return symbol_id, CONF_EXTRACTED
        if m in known:
            return m, CONF_INFERRED
        return None
    # 2. Longest known-module prefix.
    #    ``import a.b`` binds only ``a``; expanding the root bind first lets a
    #    path like ``a.b.c()`` match module ``a.b`` then symbol ``c``.
    expanded: List[str] = [path]
    root_bind = mod_imports.get(segs[0])
    if root_bind and root_bind != segs[0]:
        expanded.append(root_bind + path[len(segs[0]):])
    for cand in expanded:
        csegs = cand.split(".")
        for i in range(len(csegs), 0, -1):
            prefix = ".".join(csegs[:i])
            if prefix not in known:
                continue
            rest = csegs[i:]
            if not rest:
                return prefix, CONF_INFERRED
            symbol_id = f"{prefix}{MODULE_SEP}{'.'.join(rest)}"
            if graph.symbol_exists(symbol_id):
                return symbol_id, CONF_EXTRACTED
            return prefix, CONF_INFERRED
    # 3. Bare local symbol of the importing module.
    if len(segs) == 1:
        local = f"{module_id}{MODULE_SEP}{segs[0]}"
        if graph.symbol_exists(local):
            return local, CONF_EXTRACTED
    return None


def _coerce_parent(parent: str, module_id: str, graph: CodeGraph) -> str:
    """Map a call-site owner to a real node id.

    Methods are members of their class (no standalone node), so a call inside
    ``Cls.method`` is attributed to ``module::Cls``; a call inside a nested
    closure to its outermost named symbol; a module-level call to the module.
    The loop terminates at the module id, which is always a node.
    """
    cur = parent or module_id
    while cur not in graph.nodes and MODULE_SEP in cur:
        cur = cur.rsplit(MODULE_SEP, 1)[0]
    if cur not in graph.nodes:
        return module_id
    return cur


def _add_call_edges(graph: CodeGraph, facts_by_module, modules) -> None:
    known = set(modules)
    for module_id in modules:
        facts = facts_by_module[module_id]
        mod_imports, from_syms = _import_bindings(facts, module_id, known)
        for call in facts.calls:
            parent = _coerce_parent(call.parent, module_id, graph)
            if not call.path:
                # Dynamic callable (Subscript / nested Call): no corpus target
                # provable from the AST alone — drop rather than fabricate.
                continue
            resolved = _resolve_target(
                call.path, module_id=module_id, facts=facts, known=known,
                mod_imports=mod_imports, from_syms=from_syms, graph=graph,
            )
            if resolved is None:
                continue
            target, confidence = resolved
            if parent == target:
                continue  # no self-edges
            graph.edges.append(
                GraphEdge(source=parent, target=target, relation=EDGE_CHAMA, confidence=confidence)
            )


def _add_herda_edges(graph: CodeGraph, facts_by_module, modules) -> None:
    known = set(modules)
    for module_id in modules:
        facts = facts_by_module[module_id]
        mod_imports, from_syms = _import_bindings(facts, module_id, known)
        for base in facts.bases:
            if not base.path:
                continue
            resolved = _resolve_target(
                base.path, module_id=module_id, facts=facts, known=known,
                mod_imports=mod_imports, from_syms=from_syms, graph=graph,
            )
            if resolved is None:
                continue
            target, confidence = resolved
            if base.cls == target:
                continue
            graph.edges.append(
                GraphEdge(
                    source=base.cls, target=target,
                    relation=EDGE_HERDA, confidence=confidence,
                )
            )


# --------------------------------------------------------------------------
# God nodes / degree
# --------------------------------------------------------------------------
def degree_map(graph: CodeGraph) -> Dict[str, int]:
    """Total degree (in + out) per node id, counting edge multiplicity."""
    deg: Dict[str, int] = {}
    for e in graph.edges:
        deg[e.source] = deg.get(e.source, 0) + e.count
        deg[e.target] = deg.get(e.target, 0) + e.count
    return deg


def god_nodes(graph: CodeGraph, top: int = 10) -> List[Tuple[str, int]]:
    """Top ``top`` node ids by total degree, ties broken by node id."""
    deg = degree_map(graph)
    ranked = sorted(deg.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:top]


def degrees_for(nodes: List[str], graph: CodeGraph) -> Dict[str, int]:
    d = degree_map(graph)
    return {n: d.get(n, 0) for n in nodes}
