"""HAOS DSH (DeepSeek Harness) Subagent Delegation Tool.

Allows the agent to delegate complex, multi-step subtasks directly to DeepSeek Harness (DSH)
in the middle of an interactive CLI conversation (e.g., when the user asks:
"use o dsh para isso", "delegue para o dsh", "rode via dsh").

Executes DSH in its own isolated subagent context/workspace and streams or returns
the structured outcome back to the parent HAOS conversation.
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
) -> str:
    """Delegate a focused subtask to DeepSeek Harness (DSH).

    Args:
        objective: Clear, self-contained instruction for DSH to accomplish.
        workdir: Directory where DSH should execute (defaults to CWD).
        task_id: Active task context.
    """
    if not objective or not objective.strip():
        return json.dumps({"success": False, "error": "Objective cannot be empty."}, ensure_ascii=False)

    dsh_bin = shutil.which("dsh") or os.environ.get("DSH_PATH") or "dsh"
    target_dir = os.path.abspath(workdir) if workdir else os.getcwd()

    if not os.path.isdir(target_dir):
        return json.dumps({
            "success": False,
            "error": f"Target workdir does not exist: {target_dir}"
        }, ensure_ascii=False)

    cmd = [
        dsh_bin,
        "exec",
        "--objective", objective.strip(),
        "--workdir", target_dir,
    ]

    logger.info("Executing DSH subagent: %s (workdir: %s)", " ".join(cmd), target_dir)

    try:
        # Run DSH execution synchronously for the conversation turn
        proc = subprocess.run(
            cmd,
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=600,  # 10 min cap for autonomous subagent run
        )
        stdout_clean = proc.stdout.strip() if proc.stdout else ""
        # Tail output if excessively long to keep prompt caching healthy
        if len(stdout_clean) > 8000:
            preview = stdout_clean[:2000] + "\n\n[... output pruned ...]\n\n" + stdout_clean[-6000:]
        else:
            preview = stdout_clean

        return json.dumps({
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "output": preview,
            "workdir": target_dir,
        }, ensure_ascii=False)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "error": "DSH execution timed out after 10 minutes.",
            "workdir": target_dir,
        }, ensure_ascii=False)
    except FileNotFoundError:
        return json.dumps({
            "success": False,
            "error": f"DeepSeek Harness executable '{dsh_bin}' not found on system PATH.",
            "suggestion": "Verify DSH installation or set DSH_PATH in the environment.",
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "success": False,
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
        task_id=kw.get("task_id", "default"),
    ),
    check_fn=_check_dsh_available,
)
