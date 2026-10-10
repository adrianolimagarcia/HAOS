"""Unit tests for HAOS Preflight & Runtime Drift Validator."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hermes.platform.diagnostics.preflight_drift import (
    ASTSymbolExtractor,
    FileDrift,
    PreflightReport,
    RuntimeDriftValidator,
    SymbolMismatch,
)


def test_ast_symbol_extractor_basic():
    code = """
import os
from sys import path, argv as arguments

CONSTANT_A = 1
VAR_B, VAR_C = 2, 3
ANNTOTATED_D: int = 4

def my_func():
    pass

async def my_async_func():
    pass

class MyClass:
    pass
"""
    exports = ASTSymbolExtractor.extract_exports(code)
    expected = {
        "os",
        "path",
        "arguments",
        "CONSTANT_A",
        "VAR_B",
        "VAR_C",
        "ANNTOTATED_D",
        "my_func",
        "my_async_func",
        "MyClass",
    }
    assert expected.issubset(exports)


def test_validator_detects_missing_symbol_in_fixture(tmp_path):
    """Simulates the exact drift case: an importer expects contains_hypothesis from candidate.py,

    but candidate.py is out of sync and lacks the symbol.
    """
    repo = tmp_path / "repo"
    pkg = repo / "hermes" / "platform" / "context" / "memory"
    pkg.mkdir(parents=True)

    # Importer expects contains_hypothesis
    importer_file = pkg / "dream.py"
    importer_file.write_text(
        "from hermes.platform.context.memory.candidate import contains_hypothesis\n"
        "def run():\n"
        "    return contains_hypothesis('test')\n",
        encoding="utf-8",
    )

    # Candidate file is missing contains_hypothesis (DRIFT)
    candidate_file = pkg / "candidate.py"
    candidate_file.write_text(
        "def other_symbol():\n"
        "    pass\n",
        encoding="utf-8",
    )

    validator = RuntimeDriftValidator(repo_root=repo)
    report = validator.run_preflight(scope_dirs=["hermes/platform"], module_prefix="hermes.platform")

    assert not report.valid
    assert len(report.missing_symbols) == 1
    mismatch = report.missing_symbols[0]
    assert mismatch.symbol_name == "contains_hypothesis"
    assert mismatch.target_module == "hermes.platform.context.memory.candidate"
    assert "dream.py" in mismatch.importer_file


def test_validator_passes_when_symbol_is_present(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "hermes" / "platform" / "context" / "memory"
    pkg.mkdir(parents=True)

    importer_file = pkg / "dream.py"
    importer_file.write_text(
        "from hermes.platform.context.memory.candidate import contains_hypothesis\n",
        encoding="utf-8",
    )

    candidate_file = pkg / "candidate.py"
    candidate_file.write_text(
        "def contains_hypothesis(text):\n"
        "    return 'hypothesis' in text\n",
        encoding="utf-8",
    )

    validator = RuntimeDriftValidator(repo_root=repo)
    report = validator.run_preflight(scope_dirs=["hermes/platform"], module_prefix="hermes.platform")

    assert report.valid
    assert len(report.missing_symbols) == 0
    assert report.total_imports_checked == 1


def test_validator_detects_bundle_and_runtime_drift(tmp_path):
    """Tests file drift comparison between repo and an installed runtime / bundle dir."""
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"

    repo_pkg = repo / "hermes" / "platform"
    runtime_pkg = runtime / "hermes" / "platform"
    repo_pkg.mkdir(parents=True)
    runtime_pkg.mkdir(parents=True)

    # File unchanged
    (repo_pkg / "same.py").write_text("x = 1\n", encoding="utf-8")
    (runtime_pkg / "same.py").write_text("x = 1\n", encoding="utf-8")

    # File modified in target / drifted
    (repo_pkg / "diff.py").write_text("version = 2\n", encoding="utf-8")
    (runtime_pkg / "diff.py").write_text("version = 1\n", encoding="utf-8")

    # File missing in target
    (repo_pkg / "new_feature.py").write_text("def new_feature(): pass\n", encoding="utf-8")

    # Extra file in target
    (runtime_pkg / "deprecated_old.py").write_text("old = True\n", encoding="utf-8")

    validator = RuntimeDriftValidator(repo_root=repo, target_runtime_dir=runtime)
    report = validator.run_preflight(scope_dirs=["hermes/platform"], check_drift=True)

    assert not report.valid
    assert len(report.file_drifts) == 3

    statuses = {d.rel_path: d.status for d in report.file_drifts}
    assert statuses["hermes/platform/diff.py"] == "MODIFIED"
    assert statuses["hermes/platform/new_feature.py"] == "DELETED_OR_MISSING_IN_TARGET"
    assert statuses["hermes/platform/deprecated_old.py"] == "ADDED_IN_TARGET_NOT_IN_REPO"


def test_validator_with_target_runtime_missing_symbol(tmp_path):
    """Repo has the new code and candidate with contains_hypothesis,

    but target_runtime has old candidate.py lacking contains_hypothesis.
    The validator validating against target_runtime detects the drift!
    """
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"

    for base in (repo, runtime):
        (base / "hermes" / "platform").mkdir(parents=True)

    # Importer in repo
    (repo / "hermes" / "platform" / "dream.py").write_text(
        "from hermes.platform.candidate import contains_hypothesis\n",
        encoding="utf-8",
    )
    # Target runtime has candidate.py WITHOUT the symbol
    (runtime / "hermes" / "platform" / "candidate.py").write_text(
        "# Old version\n",
        encoding="utf-8",
    )

    validator = RuntimeDriftValidator(repo_root=repo, target_runtime_dir=runtime)
    report = validator.validate_symbols(scope_dirs=["hermes/platform"], module_prefix="hermes.platform")

    assert not report.valid
    assert len(report.missing_symbols) == 1
    assert report.missing_symbols[0].symbol_name == "contains_hypothesis"


def test_cli_script_execution(tmp_path):
    """Test haos_preflight.py CLI execution via subprocess."""
    cli_path = Path(__file__).resolve().parents[2] / "scripts" / "haos_preflight.py"

    res = subprocess.run(
        [sys.executable, str(cli_path), "--json", "--scope", "hermes/platform"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["valid"] is True
    assert data["total_files_scanned"] > 0
    assert len(data["missing_symbols"]) == 0
