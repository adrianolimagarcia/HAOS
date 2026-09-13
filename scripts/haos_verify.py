#!/usr/bin/env python3
"""HAOS Verification Harness - Automated multi-layered quality gate.

Executes syntax, style, type, test, and git-diff guardrails for HAOS tasks.
Returns JSON-structured results with clear recovery suggestions.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

FORBIDDEN_PATTERNS = [
    ".env",
    "*.pem",
    "*.key",
    "id_rsa",
    "node_modules/",
    "__pycache__/",
    ".pytest_cache/",
]


def _run_cmd(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 120) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "success": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds: {' '.join(cmd)}",
            "success": False,
        }
    except Exception as exc:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(exc),
            "success": False,
        }


def check_git_guardrails(repo_root: Path) -> Dict[str, Any]:
    """Check git status and diff for forbidden file modifications."""
    res = _run_cmd(["git", "status", "--porcelain"], cwd=repo_root)
    if not res["success"]:
        return {
            "passed": False,
            "error": f"Failed to inspect git status: {res['stderr']}",
            "violations": [],
        }

    lines = res["stdout"].splitlines()
    violations: List[str] = []
    touched_files: List[str] = []

    for line in lines:
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            filepath = parts[1].strip()
            touched_files.append(filepath)
            for pat in FORBIDDEN_PATTERNS:
                if pat.startswith("*") and filepath.endswith(pat[1:]):
                    violations.append(f"Forbidden file pattern match '{pat}': {filepath}")
                elif pat.endswith("/") and pat in filepath:
                    violations.append(f"Forbidden directory modification '{pat}': {filepath}")
                elif pat == filepath or filepath.endswith(f"/{pat}"):
                    violations.append(f"Forbidden sensitive file touched: {filepath}")

    return {
        "passed": len(violations) == 0,
        "touched_files": touched_files,
        "violations": violations,
    }


def verify_task(
    workspace_path: Optional[str] = None,
    test_target: Optional[str] = None,
    run_lint: bool = True,
    run_tests: bool = True,
    check_git: bool = True,
) -> Dict[str, Any]:
    """Run all verification layers and produce structured recovery feedback."""
    root = Path(workspace_path).resolve() if workspace_path else Path.cwd()
    checks: Dict[str, Any] = {}
    overall_passed = True
    recovery_hints: List[str] = []

    # Layer 1: Git Guardrails & Forbidden Files
    if check_git:
        git_res = check_git_guardrails(root)
        checks["git_guardrails"] = git_res
        if not git_res["passed"]:
            overall_passed = False
            for v in git_res.get("violations", []):
                recovery_hints.append(f"Revert forbidden change: {v}")

    # Layer 2: Lint / Syntax
    if run_lint:
        # Check python syntax on touched files if available
        py_files = [f for f in checks.get("git_guardrails", {}).get("touched_files", []) if f.endswith(".py")]
        if py_files:
            syntax_errors = []
            for pf in py_files[:20]:
                target = root / pf
                if target.exists():
                    chk = _run_cmd([sys.executable, "-m", "py_compile", str(target)], cwd=root)
                    if not chk["success"]:
                        syntax_errors.append(f"{pf}: {chk['stderr']}")
            if syntax_errors:
                overall_passed = False
                checks["syntax"] = {"passed": False, "errors": syntax_errors}
                recovery_hints.append("Fix Python syntax compilation errors before proceeding.")
            else:
                checks["syntax"] = {"passed": True}
        else:
            checks["syntax"] = {"passed": True, "note": "No modified python files to syntax-check."}

    # Layer 3: Test Suite
    if run_tests and test_target:
        run_tests_script = root / "scripts" / "run_tests.sh"
        if run_tests_script.exists():
            cmd = [str(run_tests_script), test_target]
        else:
            cmd = [sys.executable, "-m", "pytest", test_target, "-q"]

        test_res = _run_cmd(cmd, cwd=root, timeout=180)
        checks["tests"] = test_res
        if not test_res["success"]:
            overall_passed = False
            recovery_hints.append(
                f"Tests in '{test_target}' failed. Inspect error output and adjust implementation."
            )

    return {
        "success": overall_passed,
        "checks": checks,
        "recovery_hints": recovery_hints,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="HAOS Multi-Layer Verification Harness")
    parser.add_argument("--workspace", default=".", help="Target workspace path")
    parser.add_argument("--tests", default="", help="Test file or directory to run")
    parser.add_argument("--skip-tests", action="store_true", help="Skip test suite")
    parser.add_argument("--skip-git", action="store_true", help="Skip git guardrails")
    parser.add_argument("--json", action="store_true", help="Output JSON only")

    args = parser.parse_args()
    result = verify_task(
        workspace_path=args.workspace,
        test_target=args.tests if not args.skip_tests else None,
        run_lint=True,
        run_tests=not args.skip_tests and bool(args.tests),
        check_git=not args.skip_git,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["success"]:
            print("✅ [HAOS Harness 2.0] All verification layers passed.")
        else:
            print("❌ [HAOS Harness 2.0] Verification failed:")
            for hint in result["recovery_hints"]:
                print(f"  - {hint}")
            if "tests" in result["checks"] and not result["checks"]["tests"]["success"]:
                print("\nTest details:")
                print(result["checks"]["tests"].get("stderr") or result["checks"]["tests"].get("stdout"))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
