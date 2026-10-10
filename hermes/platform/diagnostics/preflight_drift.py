"""HAOS Preflight & Runtime Drift Validator.

Automated verification against missing symbols, broken import contracts,
and bundle / installed runtime discrepancies (without mutating production runtime).
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("haos.preflight")


@dataclass
class SymbolMismatch:
    importer_file: str
    target_module: str
    symbol_name: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FileDrift:
    rel_path: str
    status: str  # "MODIFIED", "DELETED", "ADDED"
    source_hash: Optional[str] = None
    target_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PreflightReport:
    valid: bool
    total_files_scanned: int = 0
    total_imports_checked: int = 0
    missing_symbols: List[SymbolMismatch] = field(default_factory=list)
    file_drifts: List[FileDrift] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "total_files_scanned": self.total_files_scanned,
            "total_imports_checked": self.total_imports_checked,
            "missing_symbols": [s.to_dict() for s in self.missing_symbols],
            "file_drifts": [d.to_dict() for d in self.file_drifts],
            "errors": self.errors,
        }


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


class ASTSymbolExtractor:
    """Extract top-level declared and exported symbols from Python source code via AST."""

    @staticmethod
    def extract_exports(source_code: str) -> Set[str]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return set()

        exports: Set[str] = set()

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                exports.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        exports.add(target.id)
                    elif isinstance(target, (ast.Tuple, ast.List)):
                        for elt in target.elts:
                            if isinstance(elt, ast.Name):
                                exports.add(elt.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                exports.add(node.target.id)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    exports.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    exports.add(alias.asname or alias.name.split(".")[0])

        return exports


class RuntimeDriftValidator:
    """Preflight scanner verifying symbol integrity and runtime/bundle drift."""

    def __init__(self, repo_root: Path, target_runtime_dir: Optional[Path] = None):
        self.repo_root = Path(repo_root).resolve()
        self.target_runtime_dir = Path(target_runtime_dir).resolve() if target_runtime_dir else None
        self._export_cache: Dict[Path, Set[str]] = {}

    def _build_module_map(self, base_dir: Path) -> Dict[str, Path]:
        """Maps qualified python module dot-names to local filesystem paths."""
        mapping: Dict[str, Path] = {}
        for p in base_dir.rglob("*.py"):
            parts = p.relative_to(base_dir).parts
            if any(part in (".git", ".venv", "venv", "__pycache__", "site-packages") for part in parts):
                continue
            if parts[-1] == "__init__.py":
                mod_name = ".".join(parts[:-1])
            else:
                mod_name = ".".join(parts[:-1] + (parts[-1][:-3],))
            if mod_name:
                mapping[mod_name] = p
        return mapping

    def get_module_exports(self, file_path: Path) -> Set[str]:
        if file_path in self._export_cache:
            return self._export_cache[file_path]
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            exports = ASTSymbolExtractor.extract_exports(content)
        except OSError:
            exports = set()
        self._export_cache[file_path] = exports
        return exports

    def validate_symbols(
        self,
        scope_dirs: Optional[List[str]] = None,
        module_prefix: Optional[str] = None,
    ) -> PreflightReport:
        """Scan python files and verify that all imported symbols exist in the target modules."""
        report = PreflightReport(valid=True)
        target_dir = self.target_runtime_dir or self.repo_root
        module_map = self._build_module_map(target_dir)

        scan_roots = [self.repo_root / d for d in scope_dirs] if scope_dirs else [self.repo_root]

        for scan_root in scan_roots:
            if not scan_root.exists():
                continue
            for py_path in scan_root.rglob("*.py"):
                parts = py_path.relative_to(self.repo_root).parts
                if any(part in (".git", ".venv", "venv", "__pycache__", "site-packages") for part in parts):
                    continue

                report.total_files_scanned += 1
                try:
                    tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError as exc:
                    report.errors.append(f"Syntax error in {py_path.relative_to(self.repo_root)}: {exc}")
                    report.valid = False
                    continue

                rel_importer = str(py_path.relative_to(self.repo_root))

                for node in ast.walk(tree):
                    # Check "from X import Y, Z"
                    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                        mod = node.module
                        if module_prefix and not mod.startswith(module_prefix):
                            continue

                        # If target module is defined within our mapped tree
                        if mod in module_map:
                            target_file = module_map[mod]
                            exports = self.get_module_exports(target_file)
                            for alias in node.names:
                                if alias.name == "*":
                                    continue
                                report.total_imports_checked += 1
                                if alias.name not in exports:
                                    # Could it be a submodule (e.g. from hermes.platform import context)?
                                    submod = f"{mod}.{alias.name}"
                                    if submod not in module_map:
                                        report.missing_symbols.append(
                                            SymbolMismatch(
                                                importer_file=rel_importer,
                                                target_module=mod,
                                                symbol_name=alias.name,
                                                reason=f"Symbol '{alias.name}' not found in '{target_file.name}' exports",
                                            )
                                        )
                                        report.valid = False

        return report

    def compare_trees(
        self,
        subpath: Optional[str] = None,
    ) -> List[FileDrift]:
        """Compare repo files against target_runtime_dir to detect packaging/deployment drift."""
        if not self.target_runtime_dir or not self.target_runtime_dir.exists():
            return []

        src_base = (self.repo_root / subpath) if subpath else self.repo_root
        dst_base = (self.target_runtime_dir / subpath) if subpath else self.target_runtime_dir

        drifts: List[FileDrift] = []
        if not src_base.exists() or not dst_base.exists():
            return drifts

        src_files = {
            p.relative_to(src_base): p
            for p in src_base.rglob("*.py")
            if not any(part in (".git", ".venv", "venv", "__pycache__", "site-packages") for part in p.parts)
        }
        dst_files = {
            p.relative_to(dst_base): p
            for p in dst_base.rglob("*.py")
            if not any(part in (".git", ".venv", "venv", "__pycache__", "site-packages") for part in p.parts)
        }

        all_rels = set(src_files.keys()) | set(dst_files.keys())

        for rel in sorted(all_rels):
            in_src = rel in src_files
            in_dst = rel in dst_files
            rel_str = str(Path(subpath) / rel) if subpath else str(rel)

            if in_src and not in_dst:
                drifts.append(FileDrift(rel_path=rel_str, status="DELETED_OR_MISSING_IN_TARGET"))
            elif not in_src and in_dst:
                drifts.append(FileDrift(rel_path=rel_str, status="ADDED_IN_TARGET_NOT_IN_REPO"))
            else:
                h_src = _file_hash(src_files[rel])
                h_dst = _file_hash(dst_files[rel])
                if h_src != h_dst:
                    drifts.append(
                        FileDrift(
                            rel_path=rel_str,
                            status="MODIFIED",
                            source_hash=h_src,
                            target_hash=h_dst,
                        )
                    )

        return drifts

    def run_preflight(
        self,
        scope_dirs: Optional[List[str]] = None,
        module_prefix: Optional[str] = None,
        check_drift: bool = True,
    ) -> PreflightReport:
        """Full preflight execution combining symbol integrity and tree drift."""
        report = self.validate_symbols(scope_dirs=scope_dirs, module_prefix=module_prefix)
        if check_drift and self.target_runtime_dir:
            sub = scope_dirs[0] if scope_dirs and len(scope_dirs) == 1 else None
            report.file_drifts = self.compare_trees(subpath=sub)
            if report.file_drifts:
                report.valid = False
        return report
