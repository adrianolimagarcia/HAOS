"""Local, deterministic code indexing for the HAOS Codebase Wiki.

Pass 1 of the pipeline: walk a root tree, parse every ``.py`` file with the
stdlib ``ast`` module and record *syntactic facts* that are independent of the
rest of the corpus — top-level symbols, class members, import statements,
class bases and call sites. Each file's facts are cached by content sha256 so
``--update`` re-reads only files whose bytes changed.

No third-party dependency and no LLM on this path: the module is importable in
an offline hermetic environment. Facts are emitted as plain dicts so they can
be cached to JSON losslessly and merged by ``graph.py`` in a later pass.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

# Same exclusion set as evals/codebase_navigability/static_metrics.py so the
# wiki and the navigability evals measure the same corpus.
SKIP_DIRS = {
    ".git", "node_modules", "apps", "website", "build", ".venv", "venv",
    "MagicMock", "__pycache__", ".worktrees", "dist", "evals", "skills",
    "optional-skills", "docs",
}

CACHE_VERSION = "2"


# --------------------------------------------------------------------------
# Cached per-file facts
# --------------------------------------------------------------------------
@dataclass
class SymbolFact:
    """One top-level symbol (module-level class/function)."""

    kind: str            # "class" | "function" | "async_function"
    lineno: int
    end_lineno: int
    members: List[str] = field(default_factory=list)   # class methods only


@dataclass
class ImportFact:
    """One import/from-import statement resolved to a dotted target."""

    mode: str            # "import" | "from"
    target: str          # dotted module target ("os.path" or "tools.registry")
    alias: Optional[str] # name bound in the importing namespace
    names: List[str] = field(default_factory=list)     # from-import names
    level: int = 0       # relative import level (0 = absolute)


@dataclass
class CallFact:
    """One call site, kept unresolved until the corpus-wide merge.

    ``root`` is the leading Name id and ``path`` the full dotted name starting
    from it (e.g. call ``tools.registry.dispatch(...)`` -> root ``tools``,
    path ``tools.registry.dispatch``). Root-less calls (bare ``dispatch(...)``)
    have ``root=None`` and path ``dispatch``.
    """

    parent: str          # owning symbol id ("" = module level)
    root: Optional[str]
    path: str


@dataclass
class BaseFact:
    """One class base, kept unresolved until the merge."""

    cls: str             # class symbol id (module-qualified later)
    root: Optional[str]  # leading Name id (""/None when bare Name base)
    path: str            # full dotted base name from the root
    lineno: int


@dataclass
class FileFacts:
    """All syntactic facts extracted from one file."""

    module_id: str
    sha256: str
    symbols: Dict[str, SymbolFact] = field(default_factory=dict)
    imports: List[ImportFact] = field(default_factory=list)
    calls: List[CallFact] = field(default_factory=list)
    bases: List[BaseFact] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)  # dotted ids named in text


def _code_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _binding_name(alias: Optional[ast.alias]) -> str:
    return alias.asname or alias.name.split(".")[0]


def _find_symbols(tree: ast.Module, module_id: str) -> Dict[str, SymbolFact]:
    """Top-level classes/functions (methods are members of their class)."""
    out: Dict[str, SymbolFact] = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "class"
            members: List[str] = []
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        members.append(stmt.name)
            elif isinstance(node, ast.AsyncFunctionDef):
                kind = "async_function"
            else:
                kind = "function"
            out[node.name] = SymbolFact(
                kind=kind,
                lineno=node.lineno,
                end_lineno=getattr(node, "end_lineno", node.lineno),
                members=members,
            )
    return out


def _attr_chain(node: ast.AST) -> Tuple[Optional[str], str]:
    """Resolve a Name/Attribute expression to (leading_name, dotted_path).

    ``registry.dispatch`` -> ("registry", "registry.dispatch"); a bare
    ``dispatch`` -> ("dispatch", "dispatch"); anything dynamic (Subscript,
    Call, attribute-of-call) -> (None, "") so the merge can mark it INFERRED.
    """
    if isinstance(node, ast.Name):
        return node.id, node.id
    if isinstance(node, ast.Attribute):
        root, prefix = _attr_chain(node.value)
        if root is None:
            return None, ""
        return root, f"{prefix}.{node.attr}"
    return None, ""


class _CallCollector(ast.NodeVisitor):
    """Collect call sites with the owning symbol id for each one.

    ``parent`` is the nearest symbol that has a node: a method (``Cls.m``), a
    top-level function, or "" for module-level calls. Nested closures resolve
    to their outermost named symbol so the merge can attach the call.
    """

    def __init__(self, module_id: str) -> None:
        self.module_id = module_id
        self.calls: List[CallFact] = []
        self._func_stack: List[str] = []     # symbol ids, innermost last
        self._class_stack: List[str] = []

    def _current_parent(self) -> str:
        if self._func_stack:
            return self._func_stack[-1]
        if self._class_stack:
            return self._class_stack[-1]
        return ""

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(f"{self.module_id}::{node.name}")
        self.generic_visit(node)
        self._class_stack.pop()

    def _enter_function(self, node: ast.AST) -> None:
        # Skip methods of anonymous/expression classes handled by visit_ClassDef
        if self._class_stack:
            base = self._class_stack[-1]
        else:
            base = self.module_id
        self._func_stack.append(f"{base}::{node.name}")
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, (ast.Name, ast.Attribute)):
            root, path = _attr_chain(func)
            self.calls.append(CallFact(parent=self._current_parent(), root=root, path=path))
        else:
            # func is dynamic (Subscript / nested Call / lambda): keep the
            # parent + a marker so the merge can emit an INFERRED edge only
            # when it has real evidence, else drop it.
            self.calls.append(CallFact(parent=self._current_parent(), root=None, path=""))
        self.generic_visit(node)


def _find_imports(tree: ast.Module) -> List[ImportFact]:
    out: List[ImportFact] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(ImportFact(mode="import", target=alias.name, alias=_binding_name(alias)))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [a.asname or a.name for a in node.names]
            out.append(
                ImportFact(
                    mode="from",
                    target=module,          # pure dotted module ("" for "from . import x")
                    alias=names[0] if len(names) == 1 else None,
                    names=names,
                    level=node.level,
                )
            )
    return out


def _find_bases(tree: ast.Module, module_id: str) -> List[BaseFact]:
    out: List[BaseFact] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        cls_id = f"{module_id}::{node.name}"
        for base in node.bases:
            root, path = _attr_chain(base)
            out.append(BaseFact(cls=cls_id, root=root, path=path, lineno=base.lineno))
    return out


def extract_python(text: str, module_id: str) -> FileFacts:
    """Parse one file's source into syntactic facts (never raises on syntax)."""
    facts = FileFacts(module_id=module_id, sha256=_code_sha(text))
    try:
        tree = ast.parse(text, filename=module_id)
    except (SyntaxError, ValueError, RecursionError):
        return facts  # a broken file contributes no nodes/edges
    facts.symbols = _find_symbols(tree, module_id)
    facts.imports = _find_imports(tree)
    collector = _CallCollector(module_id)
    collector.visit(tree)
    facts.calls = collector.calls
    facts.bases = _find_bases(tree, module_id)
    return facts


# --------------------------------------------------------------------------
# Markdown concept extractor (Fase 3: docs .md viram nós de conceito)
# --------------------------------------------------------------------------
_MD_HEADING_RE = None  # set lazily to avoid paying regex import at module load


def extract_markdown(text: str, module_id: str) -> FileFacts:
    """Extract concept facts from a ``.md`` file, no LLM.

    Headings ``#/##/###`` become concept symbols (deterministic slugs, deduped
    by index), and every dotted token that names a *module* (two+ dotted
    segments) is recorded as a mention so the graph pass can draw ``cita``
    edges to symbols the doc names. Fenced code blocks are skipped for mentions
    (code listings are not citations) but headings inside them are ignored too.
    """
    import re

    facts = FileFacts(module_id=module_id, sha256=_code_sha(text))
    heading_re = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$", re.MULTILINE)
    code_fence = re.compile(r"^```|^~~~", re.MULTILINE)
    dotted = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*){1,}\b")

    slug_index: Dict[str, int] = {}
    seen_slugs: Set[str] = set()
    in_fence = False
    fence_pos = 0
    for m in heading_re.finditer(text):
        # Track whether this heading sits inside a fenced block.
        fence_count = len(code_fence.findall(text, fence_pos, m.start()))
        in_fence = fence_count % 2 == 1
        fence_pos = m.start()
        if in_fence:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        slug = _slugify(title)
        if slug in seen_slugs:
            slug_index[slug] = slug_index.get(slug, 0) + 1
            slug = f"{slug}-{slug_index[slug]}"
        else:
            slug_index[slug] = 0
        seen_slugs.add(slug)
        lineno = text.count("\n", 0, m.start()) + 1
        facts.symbols[slug] = SymbolFact(kind="concept", lineno=lineno, end_lineno=lineno)

    # Mentions: dotted module/symbol ids outside fenced code, de-duplicated.
    mentioned: Set[str] = set()
    fence_ranges: List[Tuple[int, int]] = []
    start = None
    for m in code_fence.finditer(text):
        if start is None:
            start = m.start()
        else:
            fence_ranges.append((start, m.end()))
            start = None
    if start is not None:  # unclosed fence to EOF
        fence_ranges.append((start, len(text)))

    def _outside(i: int) -> bool:
        return not any(a <= i < b for a, b in fence_ranges)

    for m in dotted.finditer(text):
        if _outside(m.start()):
            mentioned.add(m.group(0))
    facts.mentions = sorted(mentioned)[:500]
    return facts


def _slugify(title: str) -> str:
    """Deterministic, file-name-safe slug for a markdown heading."""
    out: List[str] = []
    for ch in title.strip().lower():
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch in " .:/":
            out.append("-")
    slug = "".join(out).strip("-").strip(".")
    return slug or "conceito"


def py_files(root: Path, skip: Set[str]) -> Iterator[Path]:
    """Yield relative ``.py`` paths under ``root`` honouring the skip set."""
    import os

    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip]
        dp_path = Path(dp)
        for fn in sorted(fns):
            if fn.endswith(".py"):
                yield (dp_path / fn).relative_to(root)


def all_source_files(root: Path, skip: Set[str], extensions: Sequence[str]) -> Iterator[Path]:
    """Yield relative paths under ``root`` whose suffix is in ``extensions``."""
    import os

    wanted = tuple(extensions)
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip]
        dp_path = Path(dp)
        for fn in sorted(fns):
            if fn.endswith(wanted):
                yield (dp_path / fn).relative_to(root)


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------
def _cache_path(cache_dir: Path, sha: str) -> Path:
    return cache_dir / f"{CACHE_VERSION}-{sha}.json"


def load_cached(cache_dir: Path, sha: str) -> Optional[FileFacts]:
    p = _cache_path(cache_dir, sha)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return file_facts_from_dict(raw)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def save_cached(cache_dir: Path, facts: FileFacts) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(cache_dir, facts.sha256).write_text(
        json.dumps(file_facts_to_dict(facts), sort_keys=True), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# Serialization (deterministic, sort_keys)
# --------------------------------------------------------------------------
def symbol_to_dict(sym: SymbolFact) -> Dict[str, Any]:
    return {
        "kind": sym.kind,
        "lineno": sym.lineno,
        "end_lineno": sym.end_lineno,
        "members": sym.members,
    }


def symbol_from_dict(raw: Dict[str, Any]) -> SymbolFact:
    return SymbolFact(
        kind=raw["kind"],
        lineno=raw["lineno"],
        end_lineno=raw.get("end_lineno", raw["lineno"]),
        members=list(raw.get("members", [])),
    )


def file_facts_to_dict(facts: FileFacts) -> Dict[str, Any]:
    return {
        "module_id": facts.module_id,
        "sha256": facts.sha256,
        "symbols": {k: symbol_to_dict(v) for k, v in facts.symbols.items()},
        "imports": [
            {
                "mode": i.mode,
                "target": i.target,
                "alias": i.alias,
                "names": i.names,
                "level": i.level,
            }
            for i in facts.imports
        ],
        "calls": [{"parent": c.parent, "root": c.root, "path": c.path} for c in facts.calls],
        "bases": [
            {"cls": b.cls, "root": b.root, "path": b.path, "lineno": b.lineno}
            for b in facts.bases
        ],
        "mentions": facts.mentions,
    }


def file_facts_from_dict(raw: Dict[str, Any]) -> FileFacts:
    return FileFacts(
        module_id=raw["module_id"],
        sha256=raw["sha256"],
        symbols={k: symbol_from_dict(v) for k, v in raw.get("symbols", {}).items()},
        imports=[
            ImportFact(
                mode=i["mode"],
                target=i["target"],
                alias=i.get("alias"),
                names=list(i.get("names", [])),
                level=int(i.get("level", 0)),
            )
            for i in raw.get("imports", [])
        ],
        calls=[
            CallFact(parent=c.get("parent", ""), root=c.get("root"), path=c.get("path", ""))
            for c in raw.get("calls", [])
        ],
        bases=[
            BaseFact(
                cls=b["cls"], root=b.get("root"), path=b.get("path", ""),
                lineno=int(b.get("lineno", 0)),
            )
            for b in raw.get("bases", [])
        ],
        mentions=list(raw.get("mentions", [])),
    )


def scan_facts(
    root: Path,
    cache_dir: Optional[Path] = None,
    *,
    skip: Optional[Set[str]] = None,
    markdown: bool = False,
) -> Tuple[Dict[str, FileFacts], List[str], Dict[str, str]]:
    """Index ``.py`` files under ``root`` (and ``.md`` when ``markdown``).

    Returns ``(facts_by_module, freshly_parsed_rel_paths, rel_paths_by_module)``.
    With ``cache_dir`` set, a file whose content sha matches a cached entry is
    served from cache instead of being re-parsed; only new/changed files are
    parsed (and their facts persisted), so a second run after touching one file
    re-indexes just that file. ``rel_paths_by_module`` maps module id -> exact
    root-relative path so ``source_file`` is precise even for ``__init__``
    modules.

    Markdown files (opt-in) are indexed as *concept modules*: headings become
    concept symbols and dotted ids in the prose become mentions, so the graph
    can draw ``cita`` edges from docs to the code they name. A ``.md`` that
    would collide with a ``.py`` of the same dotted id is skipped.
    """
    skip_set = set(SKIP_DIRS if skip is None else skip)
    facts: Dict[str, FileFacts] = {}
    freshly_parsed: List[str] = []
    rel_paths: Dict[str, str] = {}
    sources: List[Tuple[Path, str, bool]] = [
        (rel, ".py", False) for rel in py_files(root, skip_set)
    ]
    if markdown:
        sources += [(rel, ".md", True) for rel in all_source_files(root, skip_set, (".md",))]
    sources.sort(key=lambda item: str(item[0]))
    for rel, ext, is_md in sources:
        rel_str = rel.as_posix()
        if not is_md and ext != ".py":
            continue
        module_id = _module_id_for(rel, is_md)
        if not module_id:
            continue
        if module_id in facts:
            # A .md colliding with an already-indexed module: keep the .py.
            continue
        text = (root / rel).read_text(encoding="utf-8", errors="surrogatepass")
        sha = _code_sha(text)
        rel_paths[module_id] = rel_str
        cached = load_cached(cache_dir, sha) if cache_dir is not None else None
        if cached is not None:
            facts[module_id] = cached
            continue
        f = extract_markdown(text, module_id) if is_md else extract_python(text, module_id)
        facts[module_id] = f
        freshly_parsed.append(rel_str)
        if cache_dir is not None:
            save_cached(cache_dir, f)
    return facts, freshly_parsed, rel_paths


def _module_id_for(rel: Path, is_md: bool) -> str:
    """Module id for a python or markdown source path.

    Python: dotted module id from the relative path (``a/b/c.py`` -> ``a.b.c``);
    namespace packages (no ``__init__.py``) are the norm, so the path alone is
    authoritative and a trailing ``__init__`` is dropped so the package and its
    ``__init__`` share one node (top-level ``__init__.py`` -> empty id). Markdown
    files are namespaced with a ``~md`` marker so they can never collide with a
    ``.py`` of the same stem (``README.md`` vs ``README.py``), yet still sort
    beside their sibling code modules.
    """
    parts = list(rel.parts)
    if not parts:
        return ""
    if is_md:
        return _md_module_id(parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
        return ".".join(parts)
    parts[-1] = parts[-1][:-3]  # strip ".py"
    return ".".join(parts)


def _md_module_id(parts: List[str]) -> str:
    """``docs/haos/README.md`` -> ``docs.haos.README~md`` (unambiguous, sortable)."""
    if not parts:
        return ""
    stem = parts[-1][:-3] if parts[-1].endswith(".md") else parts[-1]
    return ".".join(parts[:-1] + [stem + "~md"])
