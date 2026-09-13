"""LSP Unified Intelligence — Marco 5.

CodeSymbolGraph (symbols, definitions, references, call hierarchy) and
ImpactAnalyzer (blast radius calculation and affected test suite discovery).

Strict stdlib-only; PEP-420 namespace compliant.
"""

from __future__ import annotations

import ast
import collections
import dataclasses
import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class SymbolLocation:
    """Location of a symbol definition or reference in code."""
    file_path: str
    line: int
    character: int = 0
    end_line: Optional[int] = None
    end_character: Optional[int] = None


@dataclass
class SymbolNode:
    """Node representing a code symbol (function, class, method, variable)."""
    name: str
    kind: str  # function, class, method, variable, interface
    file_path: str
    location: SymbolLocation
    container_name: Optional[str] = None
    docstring: Optional[str] = None
    signature: Optional[str] = None

    @property
    def qualified_name(self) -> str:
        if self.container_name:
            return f"{self.container_name}.{self.name}"
        return self.name

    @property
    def id(self) -> str:
        return f"{self.file_path}::{self.qualified_name}"


@dataclass
class BlastRadius:
    """Computed impact result when modifying symbols or files."""
    modified_symbols: Set[str] = field(default_factory=set)
    modified_files: Set[str] = field(default_factory=set)
    affected_callers: Set[str] = field(default_factory=set)
    affected_references: Set[SymbolLocation] = field(default_factory=set)
    affected_files: Set[str] = field(default_factory=set)
    affected_test_suites: Set[str] = field(default_factory=set)
    depth_reached: int = 0
    severity: str = "low"  # low, medium, high, critical

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modified_symbols": sorted(list(self.modified_symbols)),
            "modified_files": sorted(list(self.modified_files)),
            "affected_callers": sorted(list(self.affected_callers)),
            "affected_references": [
                dataclasses.asdict(loc) for loc in self.affected_references
            ],
            "affected_files": sorted(list(self.affected_files)),
            "affected_test_suites": sorted(list(self.affected_test_suites)),
            "depth_reached": self.depth_reached,
            "severity": self.severity,
        }


class CodeSymbolGraph:
    """Graph structure maintaining symbols, definitions, references, and call hierarchy."""

    def __init__(self) -> None:
        # symbol_id -> SymbolNode
        self.symbols: Dict[str, SymbolNode] = {}
        # file_path -> Set[symbol_id]
        self.file_symbols: Dict[str, Set[str]] = collections.defaultdict(set)
        # symbol_id -> Set[SymbolLocation]
        self.references: Dict[str, Set[SymbolLocation]] = collections.defaultdict(set)
        # caller_id -> Set[callee_id]
        self.call_callees: Dict[str, Set[str]] = collections.defaultdict(set)
        # callee_id -> Set[caller_id]
        self.call_callers: Dict[str, Set[str]] = collections.defaultdict(set)

    def add_symbol(self, node: SymbolNode) -> None:
        """Add or update a symbol in the graph."""
        self.symbols[node.id] = node
        self.file_symbols[node.file_path].add(node.id)

    def get_symbol(self, symbol_id: str) -> Optional[SymbolNode]:
        return self.symbols.get(symbol_id)

    def find_symbols_by_name(self, name: str) -> List[SymbolNode]:
        """Find symbols matching simple name or qualified name."""
        matches = []
        for s in self.symbols.values():
            if s.name == name or s.qualified_name == name:
                matches.append(s)
        return matches

    def add_reference(self, symbol_id: str, location: SymbolLocation) -> None:
        """Record a reference location to a given symbol."""
        self.references[symbol_id].add(location)

    def get_references(self, symbol_id: str) -> Set[SymbolLocation]:
        return set(self.references.get(symbol_id, set()))

    def add_call(self, caller_id: str, callee_id: str) -> None:
        """Record that caller_id calls callee_id."""
        self.call_callees[caller_id].add(callee_id)
        self.call_callers[callee_id].add(caller_id)

    def get_callers(self, callee_id: str) -> Set[str]:
        """Return all direct callers of callee_id."""
        return set(self.call_callers.get(callee_id, set()))

    def get_callees(self, caller_id: str) -> Set[str]:
        """Return all direct callees called by caller_id."""
        return set(self.call_callees.get(caller_id, set()))

    def get_transitive_callers(self, callee_id: str, max_depth: int = 5) -> Tuple[Set[str], int]:
        """Traverse upwards to find all transitive callers up to max_depth."""
        visited: Set[str] = set()
        queue: collections.deque[Tuple[str, int]] = collections.deque([(callee_id, 0)])
        max_reached = 0

        while queue:
            curr, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for parent in self.call_callers.get(curr, set()):
                if parent not in visited:
                    visited.add(parent)
                    max_reached = max(max_reached, depth + 1)
                    queue.append((parent, depth + 1))

        return visited, max_reached

    def get_symbols_in_file(self, file_path: str) -> List[SymbolNode]:
        """Return all symbols declared in file_path."""
        sym_ids = self.file_symbols.get(file_path, set())
        return [self.symbols[sid] for sid in sym_ids if sid in self.symbols]

    def scan_directory(
        self,
        root_dir: str,
        exclude_dirs: Optional[Set[str]] = None,
        max_files: int = 1000,
    ) -> int:
        """Scan a codebase directory using native AST and populate nodes/edges."""
        excludes = exclude_dirs or {
            ".git", ".venv", "venv", "__pycache__", ".worktrees", ".haos", "dist", "build", "node_modules"
        }
        count = 0
        root_path = pathlib.Path(root_dir)

        class _ASTCallVisitor(ast.NodeVisitor):
            def __init__(self, rel_p: str, graph: CodeSymbolGraph):
                self.rel_p = rel_p
                self.graph = graph
                self.scope_stack: List[str] = []

            def visit_ClassDef(self, node: ast.ClassDef):
                self.scope_stack.append(node.name)
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self.scope_stack.append(node.name)
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self.visit_FunctionDef(node)

            def visit_Call(self, node: ast.Call):
                callee_name = None
                if isinstance(node.func, ast.Name):
                    callee_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    callee_name = node.func.attr
                if callee_name:
                    current_scope = ".".join(self.scope_stack) if self.scope_stack else "module"
                    caller_id = f"{self.rel_p}::{current_scope}"
                    self.graph.add_call(caller_id, callee_name)
                    if self.scope_stack:
                        self.graph.add_call(self.scope_stack[-1], callee_name)
                self.generic_visit(node)

        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d not in excludes and not d.startswith(".")]
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                full_path = pathlib.Path(dirpath) / fname
                try:
                    rel_path = str(full_path.relative_to(root_path))
                except Exception:
                    rel_path = str(full_path)

                try:
                    code_text = full_path.read_text(encoding="utf-8", errors="ignore")
                    tree = ast.parse(code_text, filename=rel_path)
                except Exception:
                    continue

                current_container = None
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        current_container = node.name
                        sym = SymbolNode(
                            name=node.name,
                            kind="class",
                            file_path=rel_path,
                            location=SymbolLocation(file_path=rel_path, line=node.lineno, character=node.col_offset),
                            docstring=ast.get_docstring(node),
                        )
                        self.add_symbol(sym)
                        count += 1
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        sym = SymbolNode(
                            name=node.name,
                            kind="function" if current_container is None else "method",
                            file_path=rel_path,
                            container_name=current_container,
                            location=SymbolLocation(file_path=rel_path, line=node.lineno, character=node.col_offset),
                            docstring=ast.get_docstring(node),
                        )
                        self.add_symbol(sym)
                        count += 1

                # Extract calls using scoped visitor
                visitor = _ASTCallVisitor(rel_path, self)
                visitor.visit(tree)

                if count >= max_files * 10:
                    break
        return count

    def find_path(self, start: str, target: str, max_depth: int = 15) -> Optional[List[str]]:
        """Breadth-First Search (BFS) to find the shortest call/dependency path between two symbols/components."""
        start_id = start
        target_id = target

        matches_start = self.find_symbols_by_name(start)
        if matches_start:
            start_id = matches_start[0].id
        matches_target = self.find_symbols_by_name(target)
        if matches_target:
            target_id = matches_target[0].id

        if start_id == target_id:
            return [start_id]

        queue: collections.deque[List[str]] = collections.deque([[start_id]])
        visited: Set[str] = {start_id}

        while queue:
            path = queue.popleft()
            curr = path[-1]

            if len(path) > max_depth:
                continue

            neighbors = set(self.call_callees.get(curr, set()))
            curr_sym = self.symbols.get(curr)
            if curr_sym:
                neighbors.update(self.call_callees.get(curr_sym.name, set()))

            for nxt in neighbors:
                if nxt == target_id or nxt == target or (nxt in self.symbols and self.symbols[nxt].name == target):
                    return path + [nxt]

                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(path + [nxt])

        return None

    def identify_god_components(self, top_k: int = 10) -> List[Dict[str, Any]]:
        """Identify components/files with highest coupling (fan-in + fan-out)."""
        file_coupling = collections.defaultdict(lambda: {"symbols": 0, "in_degree": 0, "out_degree": 0})

        for sym in self.symbols.values():
            fpath = sym.file_path
            file_coupling[fpath]["symbols"] += 1
            file_coupling[fpath]["in_degree"] += len(self.call_callers.get(sym.id, set())) + len(self.call_callers.get(sym.name, set()))
            file_coupling[fpath]["out_degree"] += len(self.call_callees.get(sym.id, set())) + len(self.call_callees.get(sym.name, set()))

        results = []
        for fpath, metrics in file_coupling.items():
            total = metrics["in_degree"] + metrics["out_degree"]
            results.append({
                "file_path": fpath,
                "symbols_count": metrics["symbols"],
                "in_degree": metrics["in_degree"],
                "out_degree": metrics["out_degree"],
                "coupling_score": total,
            })

        results.sort(key=lambda x: x["coupling_score"], reverse=True)
        return results[:top_k]

    def export_graph_report(self, output_dir: str = ".haos/graphify-out") -> Dict[str, str]:
        """Export graph.json and GRAPH_REPORT.md inspired by Graphify."""
        out_path = pathlib.Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        nodes = [
            {
                "id": s.id,
                "name": s.name,
                "kind": s.kind,
                "file": s.file_path,
                "line": s.location.line,
            }
            for s in self.symbols.values()
        ]
        edges = []
        for caller, callees in self.call_callees.items():
            for callee in callees:
                edges.append({"source": caller, "target": callee, "type": "calls"})

        graph_payload = {
            "summary": {
                "nodes_count": len(nodes),
                "edges_count": len(edges),
                "files_count": len(self.file_symbols),
            },
            "nodes": nodes,
            "edges": edges,
        }
        json_file = out_path / "graph.json"
        json_file.write_text(json.dumps(graph_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        gods = self.identify_god_components(top_k=5)
        report_lines = [
            "# HAOS Code Knowledge Graph Report (Graphify Engine)",
            "",
            f"**Generated Directory:** `{out_path}`",
            f"- **Total Files:** `{len(self.file_symbols)}`",
            f"- **Total Symbols (Nodes):** `{len(nodes)}`",
            f"- **Total Dependency Edges:** `{len(edges)}`",
            "",
            "## 🚨 Top Coupled Components (God Components)",
            "Files with highest fan-in/fan-out require extra caution and isolated test suites before refactoring:",
            "",
            "| File Path | Symbols | In-Degree (Callers) | Out-Degree (Callees) | Coupling Score |",
            "| :--- | :---: | :---: | :---: | :---: |",
        ]
        for g in gods:
            report_lines.append(
                f"| `{g['file_path']}` | {g['symbols_count']} | {g['in_degree']} | {g['out_degree']} | **{g['coupling_score']}** |"
            )

        report_lines.extend([
            "",
            "## 🛡️ Agent Recommendations",
            "1. Check `graph.json` before altering signatures of top coupled components.",
            "2. Use `haos graph path <source> <target>` to map dependency trajectories.",
            "3. Run `haos evolution blast-radius <file>` before committing changes to high-coupling files.",
            ""
        ])

        report_file = out_path / "GRAPH_REPORT.md"
        report_file.write_text("\n".join(report_lines), encoding="utf-8")

        return {
            "graph_json": str(json_file),
            "graph_report": str(report_file),
        }


class ImpactAnalyzer:
    """Calculates blast radius and impacted test suites for code modifications."""

    def __init__(self, graph: CodeSymbolGraph, test_patterns: Optional[List[str]] = None) -> None:
        self.graph = graph
        self.test_patterns = test_patterns or [
            "test_",
            "_test.py",
            "tests/",
            "spec_",
            "_spec.py",
        ]

    def is_test_file(self, file_path: str) -> bool:
        norm = file_path.replace("\\", "/")
        name = os.path.basename(norm)
        if norm.startswith("tests/") or "/tests/" in norm or "/test/" in norm:
            return True
        for pat in self.test_patterns:
            if pat in name or pat in norm:
                return True
        return False

    def calculate_blast_radius(
        self,
        modified_symbols: Optional[Iterable[str]] = None,
        modified_files: Optional[Iterable[str]] = None,
        max_call_depth: int = 4,
    ) -> BlastRadius:
        """Compute the blast radius given modified symbols and/or modified files."""
        symbols_to_process: Set[str] = set()
        files_set: Set[str] = set(modified_files or [])

        # Add explicitly modified symbols
        if modified_symbols:
            for s in modified_symbols:
                # Could be symbol ID or symbol name
                if s in self.graph.symbols:
                    symbols_to_process.add(s)
                else:
                    found = self.graph.find_symbols_by_name(s)
                    for sym in found:
                        symbols_to_process.add(sym.id)

        # Add symbols living in modified files
        for f in files_set:
            file_syms = self.graph.get_symbols_in_file(f)
            for sym in file_syms:
                symbols_to_process.add(sym.id)

        # Collect transitive callers, references, and affected files
        affected_callers: Set[str] = set()
        affected_refs: Set[SymbolLocation] = set()
        affected_files: Set[str] = set(files_set)
        max_depth = 0

        for sym_id in symbols_to_process:
            # 1. Direct and transitive callers
            callers, depth = self.graph.get_transitive_callers(sym_id, max_depth=max_call_depth)
            affected_callers.update(callers)
            max_depth = max(max_depth, depth)

            # 2. Direct references
            refs = self.graph.get_references(sym_id)
            affected_refs.update(refs)

        # Map callers and references to their files
        for caller_id in affected_callers:
            node = self.graph.get_symbol(caller_id)
            if node:
                affected_files.add(node.file_path)
            elif "::" in caller_id:
                affected_files.add(caller_id.split("::", 1)[0])

        for ref in affected_refs:
            affected_files.add(ref.file_path)

        # Identify affected test suites
        affected_test_suites: Set[str] = set()
        for path in affected_files:
            if self.is_test_file(path):
                affected_test_suites.add(path)

        # Infer companion test suites for modified non-test files if not already found
        for path in affected_files:
            if not self.is_test_file(path):
                companion = self._infer_companion_test(path)
                if companion:
                    affected_test_suites.add(companion)

        # Determine severity based on depth and blast count
        total_affected = len(affected_callers) + len(affected_files)
        if total_affected > 20 or max_depth >= 4:
            severity = "critical"
        elif total_affected > 8 or max_depth >= 2:
            severity = "high"
        elif total_affected > 2:
            severity = "medium"
        else:
            severity = "low"

        return BlastRadius(
            modified_symbols=symbols_to_process,
            modified_files=files_set,
            affected_callers=affected_callers,
            affected_references=affected_refs,
            affected_files=affected_files,
            affected_test_suites=affected_test_suites,
            depth_reached=max_depth,
            severity=severity,
        )

    def analyze_impact(
        self,
        modified_symbols: Optional[Iterable[str]] = None,
        modified_files: Optional[Iterable[str]] = None,
        max_call_depth: int = 4,
    ) -> BlastRadius:
        """Alias for calculate_blast_radius for high-level impact analysis workflows."""
        return self.calculate_blast_radius(
            modified_symbols=modified_symbols,
            modified_files=modified_files,
            max_call_depth=max_call_depth,
        )

    def _infer_companion_test(self, file_path: str) -> Optional[str]:
        """Find a canonical test suite corresponding to a source file."""
        norm = file_path.replace("\\", "/")
        base = os.path.basename(norm)
        stem, ext = os.path.splitext(base)

        # candidate patterns
        candidates = [
            f"tests/test_{stem}{ext}",
            f"tests/{stem}_test{ext}",
            norm.replace("hermes/", "tests/").replace(f"{stem}{ext}", f"test_{stem}{ext}"),
        ]

        # Return candidate if it is known in symbol graph or file exists
        for c in candidates:
            if c in self.graph.file_symbols or os.path.exists(c):
                return c
        return None
