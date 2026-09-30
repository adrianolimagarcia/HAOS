"""Digital Twin and Blast Radius Impact Predictor.

Constructs a living graph of the project repository:
- Files, modules, imports, and cross-dependencies
- Associated test files
- Impact prediction / Blast Radius calculation prior to executing file modifications.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class BlastRadius:
    """Quantitative impact assessment of proposed file/module modifications."""
    target_files: List[str]
    affected_dependents: List[str]
    associated_tests: List[str]
    total_impacted_files: int
    risk_score: float  # 0.0 (negligible) to 1.0 (critical core blast)
    requires_council_approval: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_files": list(self.target_files),
            "affected_dependents": list(self.affected_dependents),
            "associated_tests": list(self.associated_tests),
            "total_impacted_files": self.total_impacted_files,
            "risk_score": round(self.risk_score, 3),
            "requires_council_approval": self.requires_council_approval,
        }


class ProjectDigitalTwin:
    """Builds and queries the repository dependency graph to predict change impacts."""

    def __init__(self, root_dir: str = "."):
        self.root_dir = os.path.abspath(root_dir)
        # file_path -> set of imported module paths
        self.dependencies: Dict[str, Set[str]] = {}
        # file_path -> set of files that import this file
        self.reverse_dependencies: Dict[str, Set[str]] = {}
        self.test_files: Set[str] = set()

    def scan_repository(self, max_files: int = 500) -> None:
        """Scan python files in root_dir and map AST import relationships."""
        scanned = 0
        py_files: List[str] = []

        for root, dirs, files in os.walk(self.root_dir):
            # Ignore VCS, node_modules, build directories, virtualenvs
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in {"node_modules", "target", "build", "dist", "__pycache__", ".venv", "venv"}
            ]
            for f in files:
                if f.endswith(".py"):
                    rel = os.path.relpath(os.path.join(root, f), self.root_dir)
                    py_files.append(rel)
                    if "test" in f or "tests/" in rel:
                        self.test_files.add(rel)
                    scanned += 1
                    if scanned >= max_files:
                        break
            if scanned >= max_files:
                break

        # Build import map
        for rel in py_files:
            self.dependencies[rel] = set()
            self.reverse_dependencies.setdefault(rel, set())

        for rel in py_files:
            abs_path = os.path.join(self.root_dir, rel)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fp:
                    tree = ast.parse(fp.read(), filename=rel)
                for node in ast.walk(tree):
                    imported_mod = ""
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imported_mod = alias.name
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imported_mod = node.module

                    if imported_mod:
                        # Convert dotted module to possible relative paths
                        mod_as_path = imported_mod.replace(".", "/")
                        for candidate in py_files:
                            cand_no_ext = os.path.splitext(candidate)[0]
                            if cand_no_ext == mod_as_path or cand_no_ext.endswith(f"/{mod_as_path}"):
                                self.dependencies[rel].add(candidate)
                                self.reverse_dependencies.setdefault(candidate, set()).add(rel)
            except Exception:
                # Tolerate parse errors in non-standard syntax
                pass

    def predict_blast_radius(self, target_files: List[str]) -> BlastRadius:
        """Calculate the blast radius of modifying the given target files."""
        normalized_targets = [os.path.normpath(f) for f in target_files]
        impacted_dependents: Set[str] = set()
        queue = list(normalized_targets)
        visited = set(normalized_targets)

        # Breadth-first search along reverse dependencies
        while queue:
            curr = queue.pop(0)
            rev_deps = self.reverse_dependencies.get(curr, set())
            for dep in rev_deps:
                if dep not in visited:
                    visited.add(dep)
                    impacted_dependents.add(dep)
                    queue.append(dep)

        # Identify associated tests
        associated_tests: Set[str] = set()
        for f in visited:
            if f in self.test_files:
                associated_tests.add(f)
            else:
                # Check naming convention: test_<name>.py or <name>_test.py
                base = os.path.splitext(os.path.basename(f))[0]
                for tf in self.test_files:
                    if f"test_{base}" in tf or f"{base}_test" in tf:
                        associated_tests.add(tf)

        total_files = len(visited)
        # Risk score proportional to total impacted files and critical core keywords
        core_keywords = {"kernel", "contract", "security", "fsm", "governance", "budget"}
        touches_core = any(
            any(k in f for k in core_keywords) for f in normalized_targets
        )

        base_risk = min(1.0, total_files / 15.0)
        if touches_core:
            base_risk = min(1.0, base_risk + 0.35)

        requires_council = base_risk >= 0.70 or len(impacted_dependents) > 10 or touches_core

        return BlastRadius(
            target_files=normalized_targets,
            affected_dependents=sorted(list(impacted_dependents)),
            associated_tests=sorted(list(associated_tests)),
            total_impacted_files=total_files,
            risk_score=base_risk,
            requires_council_approval=requires_council,
        )

    def calculate_blast_radius(self, target_files: List[str]) -> BlastRadius:
        return self.predict_blast_radius(target_files)
