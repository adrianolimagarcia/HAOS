"""HAOS External Worker Adapter: DSH & ACP connector for Kanban tasks.

Enables HAOS to dispatch Kanban lane tasks to external subagent runtimes:
- DSH (DeepSeek Harness CLI / workflow runner)
- ACP (Agent Client Protocol / Claude Code / Codex / external agents)

Integrates cleanly with hermes_cli.kanban_db_dispatch without polluting
the core AIAgent loop, adhering strictly to the Footprint Ladder and Narrow Waist.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes.platform.workers.external")


@dataclass
class ExternalWorkerSpec:
    kind: str  # "dsh" | "acp" | "generic"
    executable: str
    args: List[str]
    env_vars: Dict[str, str]


def resolve_external_worker(assignee: str, task_context: Dict[str, Any]) -> Optional[ExternalWorkerSpec]:
    """Inspect task assignee or tags to determine if an external harness worker should be spawned.

    Assignee conventions:
    - 'dsh', 'dsh-worker', 'dsh:<profile>'
    - 'acp', 'acp:<server>', 'claude-code', 'codex'
    """
    assignee_clean = (assignee or "").strip().lower()
    task_id = str(task_context.get("task_id", ""))

    # Determine workspace and optional git worktree isolation (Orca ADE pattern)
    workspace = str(task_context.get("workspace", "")).strip() or os.getcwd()
    if task_context.get("isolated") or task_context.get("use_worktree") or task_context.get("workspace_type") == "worktree":
        try:
            from hermes.platform.workspaces.git_worktree import GitWorktreeManager
            repo = Path(workspace).resolve()
            mgr = GitWorktreeManager(repo_root=repo)
            if mgr.is_git_repo():
                wt_path = mgr.create_worktree(task_id=task_id)
                workspace = str(wt_path)
                logger.info("Provisioned isolated Git worktree for worker '%s' at %s", assignee_clean, wt_path)
        except Exception as exc:
            logger.warning("Could not provision isolated worktree for task %s: %s", task_id, exc)

    # DSH (DeepSeek Harness) Connector
    if assignee_clean.startswith("dsh"):
        dsh_bin = shutil.which("dsh") or os.environ.get("DSH_PATH") or "dsh"
        objective = task_context.get("title", f"Complete task {task_id}")

        args = [
            dsh_bin,
            "exec",
            "--objective", objective,
            "--workdir", workspace,
        ]
        return ExternalWorkerSpec(
            kind="dsh",
            executable=dsh_bin,
            args=args,
            env_vars={
                "HAOS_EXTERNAL_WORKER": "dsh",
                "HAOS_KANBAN_TASK_ID": task_id,
                "HAOS_WORKTREE_PATH": workspace,
            }
        )

    # ACP (Agent Client Protocol) Connector
    if assignee_clean.startswith("acp") or assignee_clean in {"claude-code", "codex"}:
        acp_bin = shutil.which("hermes-acp") or shutil.which("acp") or "acp"
        return ExternalWorkerSpec(
            kind="acp",
            executable=acp_bin,
            args=[acp_bin, "--task", task_id, "--workspace", workspace],
            env_vars={
                "HAOS_EXTERNAL_WORKER": "acp",
                "HAOS_KANBAN_TASK_ID": task_id,
                "HAOS_WORKTREE_PATH": workspace,
            }
        )

    return None


def spawn_external_worker(spec: ExternalWorkerSpec, workspace: str, env: Dict[str, str], stdout_file: Any) -> Optional[int]:
    """Spawn the external worker process and return its PID."""
    merged_env = os.environ.copy()
    merged_env.update(env)
    merged_env.update(spec.env_vars)

    try:
        proc = subprocess.Popen(
            spec.args,
            cwd=workspace if (workspace and os.path.isdir(workspace)) else None,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=subprocess.STDOUT,
            env=merged_env,
            start_new_session=True,
        )
        logger.info("Spawned external worker [%s] (pid=%d) for command: %s", spec.kind, proc.pid, " ".join(spec.args))
        return proc.pid
    except FileNotFoundError as e:
        logger.error("External worker executable not found: %s (%s)", spec.executable, e)
        raise RuntimeError(f"External worker binary '{spec.executable}' not found on PATH.") from e
    except Exception as exc:
        logger.error("Failed to spawn external worker [%s]: %s", spec.kind, exc)
        raise
