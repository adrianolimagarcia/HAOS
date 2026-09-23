"""HAOS DSH (DeepSeek Harness) Subagent Delegation Tool.

Allows the agent to delegate complex, multi-step subtasks directly to DeepSeek Harness (DSH)
in the middle of an interactive CLI conversation (e.g., when the user asks:
"use o dsh para isso", "delegue para o dsh", "rode via dsh").

Executes DSH in its own isolated subagent context/workspace and streams or returns
the structured outcome back to the parent HAOS conversation.

The installed DSH headless CLI is one-shot (``dsh --profile headless <task>``);
it exposes no cancellation option.  Timeout is therefore enforced by the parent
subprocess boundary, and cancellation remains unsupported by this adapter.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from tools.registry import registry

logger = logging.getLogger("tools.dsh_subagent")


def dsh_run_tool(
    objective: str,
    workdir: Optional[str] = None,
    task_id: str = "default",
    timeout_seconds: float = 600,
) -> str:
    """Delegate a focused subtask to DeepSeek Harness (DSH).

    Args:
        objective: Clear, self-contained instruction for DSH to accomplish.
        workdir: Directory where DSH should execute (defaults to CWD).
        task_id: Active task context.
        timeout_seconds: Maximum runtime in seconds.
    """
    if not objective or not objective.strip():
        return json.dumps({"success": False, "error": "Objective cannot be empty."}, ensure_ascii=False)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        return json.dumps({"success": False, "error": "timeout_seconds must be positive."}, ensure_ascii=False)

    dsh_bin = shutil.which("dsh") or os.environ.get("DSH_PATH") or "dsh"
    target_dir = os.path.abspath(workdir) if workdir else os.getcwd()

    if not os.path.isdir(target_dir):
        return json.dumps({
            "success": False,
            "error": f"Target workdir does not exist: {target_dir}"
        }, ensure_ascii=False)

    # DSH 0.1.x exposes task execution through the headless profile.  The
    # launcher accepts the task as a positional argument; it has no `exec`
    # subcommand or `--objective`/`--workdir` flags.  cwd is the runtime's
    # workspace selection mechanism.
    cmd = [
        dsh_bin,
        "--profile", "headless",
        objective.strip(),
    ]

    logger.info("Executing DSH subagent: %s (workdir: %s)", " ".join(cmd), target_dir)

    try:
        # Run DSH execution synchronously for the conversation turn
        proc = subprocess.run(
            cmd,
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
        stdout_clean = proc.stdout.strip() if proc.stdout else ""
        stderr_clean = proc.stderr.strip() if proc.stderr else ""
        # Tail output if excessively long to keep prompt caching healthy
        if len(stdout_clean) > 8000:
            preview = stdout_clean[:2000] + "\n\n[... output pruned ...]\n\n" + stdout_clean[-6000:]
        else:
            preview = stdout_clean

        return json.dumps({
            "success": proc.returncode == 0,
            "status": "completed" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "stdout": preview,
            "stderr": stderr_clean,
            "output": preview,
            "workdir": target_dir,
        }, ensure_ascii=False)

    except subprocess.TimeoutExpired as exc:
        return json.dumps({
            "success": False,
            "status": "timed_out",
            "error_code": "timeout",
            "error": f"DSH execution timed out after {timeout_seconds} seconds.",
            "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
            "workdir": target_dir,
        }, ensure_ascii=False)
    except FileNotFoundError:
        return json.dumps({
            "success": False,
            "status": "failed",
            "error_code": "backend_error",
            "error": f"DeepSeek Harness executable '{dsh_bin}' not found on system PATH.",
            "suggestion": "Verify DSH installation or set DSH_PATH in the environment.",
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "success": False,
            "status": "failed",
            "error_code": "backend_error",
            "error": f"DSH delegation error: {exc}",
            "workdir": target_dir,
        }, ensure_ascii=False)


DSH_RUN_SCHEMA = {
    "name": "dsh_run",
    "description": (
        "Delegate a complex, self-contained subtask to DeepSeek Harness (DSH). "
        "Use this tool when the user requests 'use o dsh para isso', 'delegue para o dsh', "
        "or asks to leverage DeepSeek Harness for an autonomous research, coding, or workflow task. "
        "DSH runs independently in its own agent harness and returns the complete result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "objective": {
                "type": "string",
                "description": "The complete, self-contained task or objective for DSH to accomplish.",
            },
            "workdir": {
                "type": "string",
                "description": "Optional working directory for DSH (defaults to current project directory).",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Maximum runtime in seconds (default 600).",
            },
        },
        "required": ["objective"],
    },
}

def _check_dsh_available() -> bool:
    """Check if dsh executable or DSH_PATH is present on system."""
    return bool(shutil.which("dsh") or os.environ.get("DSH_PATH"))

registry.register(
    name="dsh_run",
    toolset="delegation",
    schema=DSH_RUN_SCHEMA,
    handler=lambda args, **kw: dsh_run_tool(
        objective=args.get("objective", ""),
        workdir=args.get("workdir"),
        timeout_seconds=args.get("timeout_seconds", 600),
        task_id=kw.get("task_id", "default"),
    ),
    check_fn=_check_dsh_available,
)
