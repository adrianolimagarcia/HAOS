"""Tree-sitter symbol extraction for non-Python languages — a `unified_intelligence` sibling.

`CodeSymbolGraph.scan_directory` parsed Python with the stdlib ``ast`` and skipped every
other extension (``if not fname.endswith(".py"): continue``). In this repository that
hides ~3.2k ``.ts``/``.tsx`` files — the whole dashboard, desktop and TUI surface — from
the code knowledge graph.

This module adds an OPTIONAL Tree-sitter path for those languages. It is optional on
purpose: the core stays stdlib-only and an appliance without the grammar pack keeps
working exactly as before, just with Python-only coverage. Absence degrades coverage,
never correctness — every failure path here returns "not available" rather than raising
into the scanner.

Grammars come from ``tree-sitter-language-pack`` (pre-compiled wheels, no C++ toolchain).
The pack downloads each grammar once into ``<cache>/tree-sitter-language-pack/<ver>/libs``
(``cache_dir()``) and reuses it afterwards, so the FIRST scan of a language needs network
unless that cache was pre-warmed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from hermes.platform.capabilities.lsp.unified_intelligence import SymbolLocation, SymbolNode

logger = logging.getLogger(__name__)

# tools.lazy_deps feature key; pins mirror the `graph-treesitter` extra in pyproject.toml.
LAZY_FEATURE = "graphify.treesitter"

# Extension -> tree-sitter language name. Programming languages only: data/config
# formats carry no declarations, so mapping them would add noise to a symbol graph.
EXTENSION_LANGUAGES: Dict[str, str] = {
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".lua": "lua",
}

# Declaration node type -> symbol kind. Node names are per-grammar but the JS/TS family
# and the common systems languages converge on these; unknown types are simply ignored.
_DECLARATIONS: Dict[str, str] = {
    "function_declaration": "function",
    "function_definition": "function",
    "function_item": "function",  # Rust
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "class_definition": "class",
    "abstract_class_declaration": "class",
    "struct_item": "class",  # Rust
    "impl_item": "class",  # Rust
    "method_definition": "method",
    "method_declaration": "method",
    "interface_declaration": "interface",
    "trait_item": "interface",  # Rust
    "enum_declaration": "enum",
    "enum_item": "enum",
    "type_alias_declaration": "type",
    "type_declaration": "type",  # Go
}

# Call-ish node types across the supported grammars.
_CALL_NODES = frozenset({"call_expression", "call", "method_invocation", "function_call"})

# Values that make `const foo = <value>` a function declaration. Arrow functions are the
# dominant form in modern TS/JS, so a declaration-only walk would miss most of the code.
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function", "generator_function"})

_PACK: Any = None
_PACK_TRIED = False
_PACK_ERROR: Optional[str] = None


def _load_pack() -> Any:
    """Import the grammar pack, lazily installing it when `tools.lazy_deps` allows.

    Returns ``None`` (and records why) when the pack is unavailable — installs disabled,
    offline, or a failed import. The result is cached, so a failed probe is not retried
    within one process.
    """
    global _PACK, _PACK_TRIED, _PACK_ERROR
    if _PACK_TRIED:
        return _PACK
    _PACK_TRIED = True
    try:
        import tree_sitter_language_pack as pack
    except ImportError:
        try:
            from tools.lazy_deps import ensure

            ensure(LAZY_FEATURE, prompt=False)
            import tree_sitter_language_pack as pack
        except Exception as error:  # noqa: BLE001 - any failure means "unavailable"
            _PACK_ERROR = f"{type(error).__name__}: {error}"
            logger.debug("tree-sitter grammar pack unavailable: %s", _PACK_ERROR)
            return None
    except Exception as error:  # noqa: BLE001 - a broken install must not kill the scan
        _PACK_ERROR = f"{type(error).__name__}: {error}"
        return None
    _PACK = pack
    _PACK_ERROR = None
    return _PACK


def unavailable_reason() -> Optional[str]:
    """Why Tree-sitter extraction is off, or ``None`` when it is usable."""
    if _load_pack() is not None:
        return None
    return _PACK_ERROR or "tree-sitter-language-pack is not installed"


def available() -> bool:
    """True when the grammar pack imported and at least one grammar can be loaded."""
    return _load_pack() is not None


def language_for_path(file_path: str) -> Optional[str]:
    """tree-sitter language name for a path's extension, or ``None`` if unmapped."""
    dot = file_path.rfind(".")
    if dot < 0:
        return None
    return EXTENSION_LANGUAGES.get(file_path[dot:].lower())


def _node_text(node: Any) -> str:
    return node.text.decode("utf-8", errors="replace")


def _callee_name(call_node: Any) -> Optional[str]:
    """Last identifier of a call target: ``a.b.foo()`` -> ``foo``, ``foo()`` -> ``foo``."""
    target = call_node.child_by_field_name("function") or call_node.child_by_field_name("name")
    if target is None:
        return None
    if target.type in ("identifier", "property_identifier", "field_identifier", "type_identifier"):
        return _node_text(target)
    for field in ("property", "field", "name"):
        inner = target.child_by_field_name(field)
        if inner is not None and inner.type.endswith("identifier"):
            return _node_text(inner)
    return None


def extract_symbols(rel_path: str, code_text: str, graph: Any, stats: Dict[str, Any]) -> int:
    """Parse ``code_text`` and add its symbols/calls to ``graph``; returns symbols added.

    ``stats`` is updated in place with ``languages`` (language name) and ``parse_errors``
    so the caller can report coverage. Never raises: an unparseable file is skipped.
    """
    language = language_for_path(rel_path)
    if language is None:
        return 0

    pack = _load_pack()
    if pack is None:
        return 0

    try:
        parser = pack.get_parser(language)
        tree = parser.parse(code_text.encode("utf-8", errors="replace"))
    except Exception as error:  # noqa: BLE001 - a missing/broken grammar skips the file
        stats.setdefault("grammar_errors", []).append(f"{language}: {type(error).__name__}")
        logger.debug("tree-sitter parse failed for %s (%s): %s", rel_path, language, error)
        return 0

    stats.setdefault("languages", set()).add(language)
    root = tree.root_node
    if root.has_error:
        stats["parse_errors"] = stats.get("parse_errors", 0) + 1

    added = 0
    # Explicit stack, not recursion: deeply nested source must not hit the recursion limit.
    stack: list[Tuple[Any, Tuple[str, ...]]] = [(root, ())]
    while stack:
        node, scope = stack.pop()
        for child in node.children:
            kind = _DECLARATIONS.get(child.type)
            name = None
            if kind:
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    name = _node_text(name_node)
            elif child.type == "variable_declarator":
                # `const foo = () => {}` / `const foo = function () {}` — the dominant
                # function form in TS/JS, invisible to a declaration-only walk.
                value = child.child_by_field_name("value")
                if value is not None and value.type in _FUNCTION_VALUES:
                    name_node = child.child_by_field_name("name")
                    if name_node is not None and name_node.type.endswith("identifier"):
                        kind, name = "function", _node_text(name_node)

            child_scope = scope
            if kind and name:
                container = scope[-1] if scope else None
                graph.add_symbol(
                    SymbolNode(
                        name=name,
                        kind="method" if (container and kind == "function") else kind,
                        file_path=rel_path,
                        location=SymbolLocation(
                            file_path=rel_path,
                            line=child.start_point[0] + 1,
                            character=child.start_point[1],
                            end_line=child.end_point[0] + 1,
                            end_character=child.end_point[1],
                        ),
                        container_name=container,
                    )
                )
                added += 1
                child_scope = scope + (name,)

            if child.type in _CALL_NODES:
                callee = _callee_name(child)
                if callee:
                    # Mirror the Python path: a file-scoped id plus the bare enclosing
                    # symbol, so name-only lookups resolve for both languages.
                    qualified = f"{rel_path}::" + ".".join(scope) if scope else f"{rel_path}::module"
                    graph.add_call(qualified, callee)
                    if scope:
                        graph.add_call(scope[-1], callee)
                    stats["calls"] = stats.get("calls", 0) + 1

            stack.append((child, child_scope))

    return added
