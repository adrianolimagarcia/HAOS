"""HAOS Code Review Tool: Exposes haos-review (Rust native) as an agent tool.
Allows the agent or subagent to audit git diffs, PR branches, or working trees
with deterministic static analysis rules (NPE, SQLi, Secrets, Command Injection).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, Optional

from tools.registry import registry

logger = logging.getLogger("tools.haos_review")


def check_haos_review_available() -> bool:
    """Check if haos-review binary is available on PATH or /usr/local/bin."""
    return bool(shutil.which("haos-review") or os.path.isfile("/usr/local/bin/haos-review"))


def haos_code_review(
    target: Optional[str] = None,
    from_branch: Optional[str] = None,
    to_branch: Optional[str] = None,
    task_id: Optional[str] = None,
) -> str:
    """Execute haos-review in sub-millisecond Rust speed and return structured findings."""
    bin_path = shutil.which("haos-review") or "/usr/local/bin/haos-review"
    if not os.path.isfile(bin_path):
        return json.dumps({
            "success": False,
            "error": "haos-review binary not found on system."
        }, ensure_ascii=False)

    cmd = [bin_path, "diff", "--format", "json"]
    if from_branch and to_branch:
        cmd.extend(["--from", from_branch, "--to", to_branch])
    elif target:
        cmd.extend(["--target", target])

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        if res.returncode == 0 and res.stdout.strip():
            try:
                parsed = json.loads(res.stdout.strip())
                return json.dumps({
                    "success": True,
                    "report": parsed
                }, ensure_ascii=False)
            except Exception:
                return json.dumps({"success": True, "raw_output": res.stdout.strip()}, ensure_ascii=False)
        else:
            err = res.stderr.strip() or f"Process exited with code {res.returncode}"
            return json.dumps({"success": False, "error": err}, ensure_ascii=False)
    except Exception as e:
        logger.exception("Failed to execute haos-review: %s", e)
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


HAOS_REVIEW_SCHEMA: Dict[str, Any] = {
    "name": "code_review_audit",
    "description": (
        "Audit and review code in the repository using the native haos-review Rust engine. "
        "Use this tool whenever the user asks to review code ('revise o codigo', 'faca um code review', "
        "'audite o projeto', 'verifique o diff'). Scans for security vulnerabilities (hardcoded secrets, "
        "SQL injection, command injection), unhandled errors, and code quality issues."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Optional git target commit or ref (e.g., 'HEAD~1', 'HEAD'). If omitted, audits uncommitted working tree changes."
            },
            "from_branch": {
                "type": "string",
                "description": "Base branch for comparison (e.g. 'main'). Must be used together with 'to_branch'."
            },
            "to_branch": {
                "type": "string",
                "description": "Target branch for comparison (e.g. 'feature/new-api'). Must be used together with 'from_branch'."
            }
        }
    }
}

registry.register(
    name="code_review_audit",
    toolset="coding",
    schema=HAOS_REVIEW_SCHEMA,
    handler=lambda args, **kw: haos_code_review(
        target=args.get("target"),
        from_branch=args.get("from_branch"),
        to_branch=args.get("to_branch"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_haos_review_available,
    emoji="🔍",
)
