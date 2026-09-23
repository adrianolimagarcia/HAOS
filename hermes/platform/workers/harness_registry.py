"""HAOS Execution Harness Registry & Dynamic Allocator.

Manages heterogeneous execution harnesses for multi-agent teams:
- native: Internal Hermes AIAgent engine
- dsh: DeepSeek Harness (CLI / Cordis plugin bus / append-only events)
- opencode: OpenCode CLI (AST / deep repo navigation)
- agy: AGY / Wrapper-Antigravity (Google Cloud Code Assist / Gemini OAuth)
- codex: OpenAI Codex CLI / OAuth runner
- claude-code: Claude Code CLI
- acp: Agent Client Protocol (ACP daemon / external workers)

Provides:
1. Dynamic detection & health checking of tool availability on host (PATH, sockets, env vars, ports).
2. Automated allocation of external worker specs (arguments, isolated git worktrees, env variables).
3. Harness metadata consumed by worker allocation and status reporting (independent of GraphRAG memory).
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("hermes.platform.workers.harness_registry")


class HarnessError(Exception):
    """Base error for execution harnesses."""


class HarnessUnavailableError(HarnessError):
    """Raised when an execution harness is requested but not found or operational."""


@dataclass
class HarnessInfo:
    """Diagnostic and runtime metadata for an execution harness."""
    name: str
    available: bool
    status: str  # "available" | "missing" | "unsupported" | "degraded"
    executable: Optional[str] = None
    endpoint: Optional[str] = None
    description: str = ""
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "status": self.status,
            "executable": self.executable,
            "endpoint": self.endpoint,
            "description": self.description,
            "error": self.error,
            "details": self.details,
        }


@dataclass
class ExternalWorkerSpec:
    """Execution parameters for an allocated external worker process."""
    kind: str  # "native" | "dsh" | "opencode" | "agy" | "acp" | "generic"
    executable: str
    args: List[str]
    env_vars: Dict[str, str] = field(default_factory=dict)
    workspace: str = ""


class HarnessRegistry:
    """Registry and allocator for execution harnesses."""

    _instance: Optional[HarnessRegistry] = None

    def __init__(self):
        self._custom_detectors: Dict[str, Callable[[], HarnessInfo]] = {}

    @classmethod
    def get_instance(cls) -> HarnessRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_detector(self, name: str, detector: Callable[[], HarnessInfo]) -> None:
        """Register a custom harness detection callback."""
        self._custom_detectors[name.lower().strip()] = detector

    def detect(self, harness_name: Optional[str]) -> HarnessInfo:
        """Dynamically detect and probe the specified execution harness.

        When the harness is not declared on PATH / env vars, HAOS triggers a
        bounded filesystem auto-discovery: if the tool is found locally, a
        launcher is installed into ~/.haos/bin (PATH) and the harness becomes
        available; if not, a warning is emitted and the caller is expected to
        continue with the native local worker (status "missing", error carries
        the guidance).
        """
        name = (harness_name or "native").strip().lower()

        if name in self._custom_detectors:
            try:
                return self._custom_detectors[name]()
            except Exception as exc:
                return HarnessInfo(
                    name=name,
                    available=False,
                    status="missing",
                    error=f"Custom detector error: {exc}",
                )

        if name in ("native", "hermes", "internal"):
            return HarnessInfo(
                name="native",
                available=True,
                status="available",
                executable="hermes",
                description="Native Hermes Agent Engine (default)",
            )

        if name == "dsh":
            info = self._detect_dsh()
        elif name == "opencode":
            info = self._detect_opencode()
        elif name == "agy":
            info = self._detect_agy()
        elif name in ("codex", "openai-codex"):
            info = self._detect_codex()
        elif name in ("claude-code", "claude"):
            info = self._detect_claude_code()
        elif name in ("acp", "hermes-acp"):
            info = self._detect_acp()
        else:
            # Generic probe on system PATH
            info = self._detect_generic(name)

        if info.available:
            return info

        # Not declared anywhere -> attempt bounded filesystem auto-discovery.
        try:
            from hermes.platform.workers.harness_discovery import get_discovery
            discovered = get_discovery().discover(name)
        except Exception as exc:  # discovery must never break detection
            logger.warning("Harness auto-discovery error for '%s': %s", name, exc)
            discovered = None

        if discovered is not None and discovered.available:
            return discovered
        if discovered is not None and discovered.error and not info.error:
            info.error = discovered.error
        if discovered is not None and discovered.details:
            info.details = {**info.details, **discovered.details}
        return info

    def _detect_dsh(self) -> HarnessInfo:
        # Check PATH, DSH_PATH, ~/.haos/bin/dsh, or reference checkout
        env_path = os.environ.get("DSH_PATH")
        which_path = shutil.which("dsh")
        home_dsh = Path.home() / ".haos" / "bin" / "dsh"

        executable = None
        if env_path and (os.path.isfile(env_path) or shutil.which(env_path)):
            executable = env_path
        elif which_path:
            executable = which_path
        elif home_dsh.is_file() and os.access(home_dsh, os.X_OK):
            executable = str(home_dsh)

        if executable:
            return HarnessInfo(
                name="dsh",
                available=True,
                status="available",
                executable=executable,
                description="DeepSeek Harness (Cordis Plugin Bus & Step Lifecycle)",
            )

        return HarnessInfo(
            name="dsh",
            available=False,
            status="missing",
            description="DeepSeek Harness (Cordis Plugin Bus & Step Lifecycle)",
            error="Executable 'dsh' not found on PATH or DSH_PATH. Compile via 'pnpm run build' in deepseek-harness or set DSH_PATH.",
        )

    def _detect_opencode(self) -> HarnessInfo:
        env_path = os.environ.get("OPENCODE_PATH")
        which_path = shutil.which("opencode")
        home_opencode = Path.home() / ".haos" / "bin" / "opencode"

        executable = None
        if env_path and (os.path.isfile(env_path) or shutil.which(env_path)):
            executable = env_path
        elif which_path:
            executable = which_path
        elif home_opencode.is_file() and os.access(home_opencode, os.X_OK):
            executable = str(home_opencode)

        if executable:
            return HarnessInfo(
                name="opencode",
                available=True,
                status="available",
                executable=executable,
                description="OpenCode CLI (Semantic AST & Code Intelligence)",
            )

        return HarnessInfo(
            name="opencode",
            available=False,
            status="missing",
            description="OpenCode CLI (Semantic AST & Code Intelligence)",
            error="Executable 'opencode' not found on PATH or OPENCODE_PATH.",
        )

    def _detect_agy(self) -> HarnessInfo:
        env_path = os.environ.get("AGY_PATH")
        which_path = shutil.which("agy")
        home_agy = Path.home() / ".haos" / "bin" / "agy"

        executable = None
        if env_path and (os.path.isfile(env_path) or shutil.which(env_path)):
            executable = env_path
        elif which_path:
            executable = which_path
        elif home_agy.is_file() and os.access(home_agy, os.X_OK):
            executable = str(home_agy)

        # AGY is a real CLI worker.  Do not infer availability from the
        # separate wrapper-antigravity proxy: it has a different contract.
        details = {"cli_available": bool(executable)}

        if executable:
            return HarnessInfo(
                name="agy",
                available=True,
                status="available",
                executable=executable,
                description="AGY / Antigravity CLI (non-interactive print mode)",
                details=details,
            )

        return HarnessInfo(
            name="agy",
            available=False,
            status="missing",
            description="AGY / Antigravity CLI (non-interactive print mode)",
            error="Executable 'agy' not found on PATH or AGY_PATH.",
            details=details,
        )

    def _detect_codex(self) -> HarnessInfo:
        env_path = os.environ.get("CODEX_PATH")
        which_path = shutil.which("codex")
        auth_file = Path.home() / ".codex" / "auth.json"

        executable = None
        if env_path and (os.path.isfile(env_path) or shutil.which(env_path)):
            executable = env_path
        elif which_path:
            executable = which_path

        available = bool(executable or auth_file.is_file())
        return HarnessInfo(
            name="codex",
            available=available,
            status="available" if available else "missing",
            executable=executable or "codex",
            description="OpenAI Codex CLI / OAuth runner",
            error=None if available else "Executable 'codex' not found on PATH.",
            details={"auth_file_present": auth_file.is_file()},
        )

    def _detect_claude_code(self) -> HarnessInfo:
        env_path = os.environ.get("CLAUDE_CODE_PATH")
        which_path = shutil.which("claude")

        executable = env_path if (env_path and shutil.which(env_path)) else which_path
        available = bool(executable)
        return HarnessInfo(
            name="claude-code",
            available=available,
            status="available" if available else "missing",
            executable=executable,
            description="Anthropic Claude Code CLI",
            error=None if available else "Executable 'claude' not found on PATH or CLAUDE_CODE_PATH.",
        )

    def _detect_acp(self) -> HarnessInfo:
        which_path = shutil.which("hermes-acp") or shutil.which("acp")
        available = bool(which_path)
        return HarnessInfo(
            name="acp",
            available=available,
            status="available" if available else "missing",
            executable=which_path,
            description="Agent Client Protocol (ACP) Daemon",
            error=None if available else "Executable 'hermes-acp' or 'acp' not found on PATH.",
        )

    def _detect_generic(self, name: str) -> HarnessInfo:
        which_path = shutil.which(name)
        available = bool(which_path)
        return HarnessInfo(
            name=name,
            available=available,
            status="available" if available else "missing",
            executable=which_path,
            description=f"Generic external tool: {name}",
            error=None if available else f"Executable '{name}' not found on system PATH.",
        )

    def list_all(self) -> Dict[str, HarnessInfo]:
        """Detect and return diagnostic information for all standard harnesses."""
        names = ["native", "dsh", "opencode", "agy", "codex", "claude-code", "acp"]
        for custom_name in self._custom_detectors:
            if custom_name not in names:
                names.append(custom_name)
        return {name: self.detect(name) for name in names}

    def allocate(self, harness_name: str, task_context: Dict[str, Any]) -> ExternalWorkerSpec:
        """Automatically allocate and prepare an execution harness for a task.

        Args:
            harness_name: Identifier of the harness ("dsh", "opencode", "agy", "native", etc.)
            task_context: Task metadata (task_id, title, workspace, isolated, etc.)

        Returns:
            ExternalWorkerSpec configured for invocation.

        Raises:
            HarnessUnavailableError: If the harness is missing from the system.
        """
        h_clean = (harness_name or "native").strip().lower()
        info = self.detect(h_clean)

        if not info.available:
            raise HarnessUnavailableError(
                f"Execution harness '{h_clean}' is not available on this host. "
                f"Status: {info.status}. Diagnostic: {info.error}"
            )

        task_id = str(task_context.get("task_id", "task-default"))
        objective = task_context.get("title") or task_context.get("goal") or f"Execute task {task_id}"

        # Resolve workspace and optional Git worktree isolation
        workspace = str(task_context.get("workspace", "")).strip() or os.getcwd()
        if task_context.get("isolated") or task_context.get("use_worktree") or task_context.get("workspace_type") == "worktree":
            try:
                from hermes.platform.workspaces.git_worktree import GitWorktreeManager
                repo = Path(workspace).resolve()
                mgr = GitWorktreeManager(repo_root=repo)
                if mgr.is_git_repo():
                    wt_path = mgr.create_worktree(task_id=task_id)
                    workspace = str(wt_path)
                    logger.info("Allocated isolated Git worktree for harness '%s' at %s", h_clean, wt_path)
            except Exception as exc:
                logger.warning("Could not provision isolated worktree for task %s: %s", task_id, exc)

        executable = info.executable or h_clean

        if h_clean == "native":
            return ExternalWorkerSpec(
                kind="native",
                executable=executable,
                args=["hermes", "chat", objective],
                env_vars={
                    "HAOS_EXTERNAL_WORKER": "native",
                    "HAOS_HARNESS": "native",
                    "HAOS_KANBAN_TASK_ID": task_id,
                    "HAOS_WORKTREE_PATH": workspace,
                },
                workspace=workspace,
            )

        if h_clean == "dsh":
            # DSH 0.1.x has no exec/--objective/--workdir surface.  The
            # headless profile accepts one positional task and inherits cwd.
            return ExternalWorkerSpec(
                kind="dsh",
                executable=executable,
                args=[executable, "--profile", "headless", objective],
                env_vars={
                    "HAOS_EXTERNAL_WORKER": "dsh",
                    "HAOS_HARNESS": "dsh",
                    "HAOS_KANBAN_TASK_ID": task_id,
                    "HAOS_WORKTREE_PATH": workspace,
                },
                workspace=workspace,
            )

        if h_clean == "opencode":
            return ExternalWorkerSpec(
                kind="opencode",
                executable=executable,
                args=[executable, "run", "--prompt", objective, "--dir", workspace],
                env_vars={
                    "HAOS_EXTERNAL_WORKER": "opencode",
                    "HAOS_HARNESS": "opencode",
                    "HAOS_KANBAN_TASK_ID": task_id,
                    "HAOS_WORKTREE_PATH": workspace,
                },
                workspace=workspace,
            )

        if h_clean == "agy":
            # `agy chat` is not a supported command.  The stable automation
            # surface is print mode; keep the prompt attached to --print so
            # argparse cannot consume the following option as its value.
            args = [executable, "--print", f"{objective}", "--add-dir", workspace]
            model = task_context.get("model")
            agent = task_context.get("agent")
            effort = task_context.get("effort")
            if model:
                args.extend(["--model", str(model)])
            if agent:
                args.extend(["--agent", str(agent)])
            if effort:
                args.extend(["--effort", str(effort)])
            return ExternalWorkerSpec(
                kind="agy",
                executable=executable,
                args=args,
                env_vars={
                    "HAOS_EXTERNAL_WORKER": "agy",
                    "HAOS_HARNESS": "agy",
                    "HAOS_KANBAN_TASK_ID": task_id,
                    "HAOS_WORKTREE_PATH": workspace,
                },
                workspace=workspace,
            )

        if h_clean in ("acp", "claude-code", "codex"):
            acp_bin = executable if executable and h_clean == "acp" else (shutil.which("hermes-acp") or shutil.which("acp") or "acp")
            return ExternalWorkerSpec(
                kind="acp",
                executable=acp_bin,
                args=[acp_bin, "--task", task_id, "--workspace", workspace],
                env_vars={
                    "HAOS_EXTERNAL_WORKER": h_clean,
                    "HAOS_HARNESS": h_clean,
                    "HAOS_KANBAN_TASK_ID": task_id,
                    "HAOS_WORKTREE_PATH": workspace,
                },
                workspace=workspace,
            )

        # Generic external worker
        return ExternalWorkerSpec(
            kind="generic",
            executable=executable,
            args=[executable, "--objective", objective, "--workspace", workspace],
            env_vars={
                "HAOS_EXTERNAL_WORKER": h_clean,
                "HAOS_HARNESS": h_clean,
                "HAOS_KANBAN_TASK_ID": task_id,
                "HAOS_WORKTREE_PATH": workspace,
            },
            workspace=workspace,
        )
