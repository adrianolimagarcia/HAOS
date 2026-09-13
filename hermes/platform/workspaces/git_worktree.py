"""GitWorktreeManager — Ephemeral Git Worktrees for Parallel Agent Fleet Isolation.

Inspired by Orca ADE and HAOS Multi-Agent Lane Execution:
- Allocates zero-copy, isolated ephemeral git worktrees under `.worktrees/task-<task_id>`.
- Allows multiple autonomous coding agents and external workers (DSH, ACP, Codex)
  to work concurrently on independent branches without workspace collisions or merge corruption.
- Operates 100% locally with zero cloud, Docker or network requirements.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


class GitWorktreeError(RuntimeError):
    pass


class GitWorktreeManager:
    """Manages lifecycle, isolation and safe merging of local ephemeral Git worktrees."""

    def __init__(self, repo_root: Optional[Path] = None):
        if repo_root is None:
            repo_root = Path(os.getcwd())
        self.repo_root = Path(repo_root).resolve()
        self.worktrees_dir = self.repo_root / ".worktrees"

    def _run_git(self, *args: str, cwd: Optional[Path] = None, check: bool = True) -> str:
        cmd = ["git", *args]
        target_cwd = str(cwd or self.repo_root)
        res = subprocess.run(
            cmd,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and res.returncode != 0:
            raise GitWorktreeError(
                f"git command failed: {' '.join(cmd)}\nstderr: {res.stderr.strip()}"
            )
        return res.stdout.strip()

    def get_current_branch(self) -> str:
        """Get the active branch of the main repository."""
        try:
            branch = self._run_git("branch", "--show-current", check=False)
            if branch:
                return branch
        except Exception:
            pass
        return "HEAD"

    def is_git_repo(self) -> bool:
        """Check whether repo_root is a valid git repository."""
        try:
            out = self._run_git("rev-parse", "--is-inside-work-tree", check=False)
            return out.lower() == "true"
        except Exception:
            return False

    def create_worktree(
        self,
        task_id: str,
        base_branch: Optional[str] = None,
    ) -> Path:
        """Create an ephemeral worktree and dedicated branch for an agent task.

        If base_branch is None, defaults to the current active branch of the repository.
        """
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        worktree_path = (self.worktrees_dir / f"task-{task_id}").resolve()
        branch_name = f"haos/task-{task_id}"

        # If worktree already exists, reuse it
        if worktree_path.exists():
            return worktree_path

        base_ref = base_branch or self.get_current_branch()

        # Check if the branch already exists
        branch_exists = False
        try:
            self._run_git("rev-parse", "--verify", f"refs/heads/{branch_name}")
            branch_exists = True
        except Exception:
            branch_exists = False

        if branch_exists:
            # Reattach existing branch to new worktree path
            self._run_git("worktree", "add", str(worktree_path), branch_name)
        else:
            # Create fresh branch from base_ref
            self._run_git("worktree", "add", "-b", branch_name, str(worktree_path), base_ref)

        return worktree_path

    def list_worktrees(self) -> List[Dict[str, Any]]:
        """List all active git worktrees with task metadata and cleanliness."""
        raw = self._run_git("worktree", "list", "--porcelain", check=False)
        if not raw:
            return []

        worktrees: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                if current and "path" in current:
                    worktrees.append(current)
                    current = {}
                continue

            parts = line.split(" ", 1)
            key = parts[0]
            val = parts[1] if len(parts) > 1 else ""

            if key == "worktree":
                current["path"] = val
                current["is_main"] = (Path(val).resolve() == self.repo_root)
                # Extract task_id from path if present
                m = re.search(r"task-([a-zA-Z0-9_\-]+)$", val)
                current["task_id"] = m.group(1) if m else None
            elif key == "HEAD":
                current["head"] = val
            elif key == "branch":
                current["branch"] = val.replace("refs/heads/", "")
            elif key == "detached":
                current["detached"] = True

        if current and "path" in current:
            worktrees.append(current)

        # Inspect cleanliness for each worktree
        for wt in worktrees:
            wt_path = Path(wt["path"])
            if wt_path.exists():
                status_raw = self._run_git("status", "--porcelain", cwd=wt_path, check=False)
                wt["is_clean"] = len(status_raw.strip()) == 0
            else:
                wt["is_clean"] = False

        return worktrees

    def get_diff(self, task_id: str, base_branch: Optional[str] = None) -> str:
        """Get diff of changes in the task worktree against the base branch."""
        base = base_branch or self.get_current_branch()
        branch_name = f"haos/task-{task_id}"
        return self._run_git("diff", f"{base}...{branch_name}", check=False)

    def is_clean(self, task_id: str) -> bool:
        """Check if a specific task's worktree has zero uncommitted changes."""
        worktree_path = (self.worktrees_dir / f"task-{task_id}").resolve()
        if not worktree_path.exists():
            return False
        status_raw = self._run_git("status", "--porcelain", cwd=worktree_path, check=False)
        return len(status_raw.strip()) == 0

    def remove_worktree(self, task_id: str, force: bool = True, delete_branch: bool = False) -> bool:
        """Safely remove the task worktree directory and prune git records."""
        worktree_path = (self.worktrees_dir / f"task-{task_id}").resolve()
        branch_name = f"haos/task-{task_id}"

        if not worktree_path.exists():
            try:
                self._run_git("worktree", "prune", check=False)
            except Exception:
                pass
            return False

        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(worktree_path))

        try:
            self._run_git(*args)
        except Exception:
            shutil.rmtree(worktree_path, ignore_errors=True)
            self._run_git("worktree", "prune", check=False)

        if delete_branch:
            try:
                self._run_git("branch", "-D", branch_name, check=False)
            except Exception:
                pass

        return True

    def merge_worktree(
        self,
        task_id: str,
        target_branch: Optional[str] = None,
        squash: bool = False,
    ) -> Dict[str, Any]:
        """Merge task branch into target branch."""
        branch_name = f"haos/task-{task_id}"
        target = target_branch or self.get_current_branch()

        current_branch = self.get_current_branch()
        if current_branch != target:
            self._run_git("checkout", target)

        merge_args = ["merge", branch_name, "--no-edit"]
        if squash:
            merge_args = ["merge", "--squash", branch_name]

        try:
            out = self._run_git(*merge_args)
            return {
                "success": True,
                "task_id": task_id,
                "target_branch": target,
                "output": out,
            }
        except GitWorktreeError as exc:
            return {
                "success": False,
                "task_id": task_id,
                "target_branch": target,
                "error": str(exc),
            }
