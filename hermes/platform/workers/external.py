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

from hermes.platform.workers.harness_registry import (
    ExternalWorkerSpec,
    HarnessRegistry,
    HarnessUnavailableError,
)

logger = logging.getLogger("hermes.platform.workers.external")


def resolve_external_worker(assignee: str, task_context: Dict[str, Any]) -> Optional[ExternalWorkerSpec]:
    """Inspect task assignee, harness, or tags to determine if an external harness worker should be spawned.

    Assignee conventions:
    - 'dsh', 'dsh-worker', 'dsh:<profile>'
    - 'opencode', 'opencode:<profile>'
    - 'agy', 'antigravity'
    - 'acp', 'acp:<server>', 'claude-code', 'codex'
    """
    assignee_clean = (assignee or "").strip().lower()
    if not assignee_clean:
        harness_override = task_context.get("harness")
        if harness_override and harness_override != "native":
            assignee_clean = str(harness_override).strip().lower()
        else:
            return None

    harness_target = None
    if assignee_clean.startswith("dsh"):
        harness_target = "dsh"
    elif assignee_clean.startswith("opencode"):
        harness_target = "opencode"
    elif assignee_clean.startswith("agy") or assignee_clean.startswith("antigravity"):
        harness_target = "agy"
    elif assignee_clean in {"claude-code", "claude"}:
        harness_target = "claude-code"
    elif assignee_clean in {"codex", "openai-codex"}:
        harness_target = "codex"
    elif assignee_clean.startswith("acp"):
        harness_target = "acp"
    elif task_context.get("harness") and task_context.get("harness") != "native":
        harness_target = str(task_context.get("harness")).strip().lower()

    if not harness_target:
        return None

    try:
        registry = HarnessRegistry.get_instance()
        return registry.allocate(harness_target, task_context)
    except HarnessUnavailableError as exc:
        # Tool truly absent after auto-discovery: warn loudly and continue with
        # the native local worker (return None => Kanban dispatcher falls back).
        logger.warning(
            "Harness '%s' unavailable after auto-discovery for task %s. "
            "Continuing with the native local worker. Diagnostic: %s",
            harness_target, task_context.get("task_id", "?"), exc,
        )
        return None
    except Exception as exc:
        logger.error("Error allocating harness '%s': %s", harness_target, exc)
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
