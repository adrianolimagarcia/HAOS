"""Automatic Garbage Collector / Housekeeper for subagent worktrees.

Reclaims stale or already-merged subagent worktrees under ``.worktrees/``,
ensuring disk space is freed safely without losing unmerged work.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from hermes_cli._subprocess_compat import harden_git_argv, noninteractive_git_env

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30


def _run_git(args: List[str], cwd: str, timeout: int = _GIT_TIMEOUT) -> subprocess.CompletedProcess:
    env = noninteractive_git_env()
    argv = harden_git_argv(["git", *args])
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _get_merged_branches(repo_root: str) -> Set[str]:
    """Return branch names merged into main or HEAD."""
    merged: Set[str] = set()
    for target in ("main", "HEAD"):
        res = _run_git(["branch", "--merged", target], cwd=repo_root)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                clean = line.strip().lstrip("*+ ").strip()
                if clean:
                    merged.add(clean)
    return merged


def _get_worktree_mtime(path: str) -> float:
    """Return newest mtime among modified or uncommitted files in the worktree.

    Checks files reported as modified/untracked by git status, or fallback to the worktree root.
    """
    proc = _run_git(["status", "--porcelain", "-uall"], cwd=path)
    latest = 0.0
    if proc.returncode == 0 and proc.stdout.strip():
        for line in proc.stdout.splitlines():
            # Line format is 'XY filename' or 'XY "filename"' or 'R  orig -> new'
            parts = line[3:].strip().split(" -> ")
            fname = parts[-1].strip().strip('"')
            fpath = os.path.join(path, fname)
            try:
                mt = os.path.getmtime(fpath)
                if mt > latest:
                    latest = mt
            except OSError:
                continue

    return latest


def _get_last_commit_timestamp(path: str) -> float:
    """Return unix timestamp of the last commit in the worktree (newest of author and committer dates)."""
    res = _run_git(["log", "-1", "--format=%ct|%at"], cwd=path)
    if res.returncode == 0 and res.stdout.strip():
        parts = res.stdout.strip().split("|")
        timestamps = []
        for p in parts:
            if p.isdigit():
                timestamps.append(float(p))
        if timestamps:
            return max(timestamps)
    return 0.0


def garbage_collect_subagent_worktrees(
    repo_root: Optional[str] = None,
    max_age_hours: float = 72.0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Housekeeping for subagent worktrees.

    - Identifies all worktrees corresponding to subagents (subagent-*).
    - If branch is merged to main or HEAD: safe prune (remove worktree + branch).
    - If not merged:
      - If clean (0 commits and not dirty): safe prune (remove worktree + branch).
      - If has commits or dirty:
        - If age < max_age_hours: preserve in active list.
        - If age >= max_age_hours: preserve on disk, report in stale_orphans.
    """
    from tools.subagent_worktree import resolve_repo_root

    actual_root = resolve_repo_root(repo_root or os.getcwd())
    summary: Dict[str, Any] = {
        "merged_pruned": 0,
        "clean_pruned": 0,
        "preserved_active": [],
        "stale_orphans": [],
    }

    if not actual_root or not os.path.isdir(actual_root):
        return summary

    worktrees_dir = Path(actual_root) / ".worktrees"
    if not worktrees_dir.is_dir():
        return summary

    # Parse git worktree list --porcelain to find registered worktrees
    wt_proc = _run_git(["worktree", "list", "--porcelain"], cwd=actual_root)
    if wt_proc.returncode != 0:
        logger.debug("Failed to list worktrees in %s: %s", actual_root, wt_proc.stderr)
        return summary

    registered_wts: Dict[str, Dict[str, Any]] = {}
    current: Dict[str, Any] = {}
    for line in wt_proc.stdout.splitlines():
        if line.startswith("worktree "):
            current = {"path": line[9:].strip(), "branch": None, "bare": False, "detached": False}
            registered_wts[current["path"]] = current
        elif current:
            if line.startswith("branch "):
                current["branch"] = line[7:].strip().replace("refs/heads/", "", 1)
            elif line == "bare":
                current["bare"] = True
            elif line == "detached":
                current["detached"] = True

    merged_branches = _get_merged_branches(actual_root)
    now = time.time()
    max_age_seconds = max_age_hours * 3600.0

    # Iterate over directories matching subagent-* under .worktrees
    try:
        entries = sorted(worktrees_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return summary

    for entry in entries:
        if not entry.is_dir() or not entry.name.startswith("subagent-"):
            continue

        entry_path_str = str(entry.resolve())
        # Check against registered worktrees
        wt_info = registered_wts.get(entry_path_str) or registered_wts.get(str(entry))
        branch = wt_info.get("branch") if wt_info else None

        if not branch:
            # Fallback: check git symbolic-ref in worktree
            ref_proc = _run_git(["symbolic-ref", "--short", "HEAD"], cwd=entry_path_str)
            if ref_proc.returncode == 0 and ref_proc.stdout.strip():
                branch = ref_proc.stdout.strip()
            else:
                branch = f"hermes-subagent/{entry.name}"

        # Check dirty
        status_proc = _run_git(["status", "--porcelain"], cwd=entry_path_str)
        is_dirty = bool(status_proc.returncode != 0 or status_proc.stdout.strip())

        # Check commits ahead of main/HEAD or base
        commits_ahead = 0
        base_targets = []
        if _run_git(["rev-parse", "--verify", "main"], cwd=actual_root).returncode == 0:
            base_targets.append("main")
        base_targets.append("HEAD")

        measured_commits = False
        for target in base_targets:
            ahead_proc = _run_git(["rev-list", "--count", f"{target}..HEAD"], cwd=entry_path_str)
            if ahead_proc.returncode == 0 and ahead_proc.stdout.strip().isdigit():
                commits_ahead = int(ahead_proc.stdout.strip())
                measured_commits = True
                break
        if not measured_commits:
            commits_ahead = 1

        # Check commits on branch not reachable from base_targets (behind count)
        behind_base = 0
        for target in base_targets:
            behind_proc = _run_git(["rev-list", "--count", f"HEAD..{target}"], cwd=entry_path_str)
            if behind_proc.returncode == 0 and behind_proc.stdout.strip().isdigit():
                behind_base = int(behind_proc.stdout.strip())
                break

        # a) Check if branch was merged to main or HEAD (had commits and was merged)
        is_merged = False
        if commits_ahead == 0 and behind_base > 0:
            # The branch tip is strictly an ancestor of main/HEAD with commits behind
            is_merged = True
        elif branch in merged_branches:
            # Only count as merged if it actually had diverged/was merged, or if branch is merged
            # But if commits_ahead == 0 and behind_base == 0, it is identical to base
            pass

        # If branch is merged and was an actual branch that was merged
        if is_merged:
            if not dry_run:
                _run_git(["worktree", "remove", "--force", entry_path_str], cwd=actual_root)
                _run_git(["branch", "-D", branch], cwd=actual_root)
                if entry.exists():
                    shutil.rmtree(entry, ignore_errors=True)
            summary["merged_pruned"] += 1
            logger.info("Merged subagent worktree pruned: %s (branch: %s)", entry_path_str, branch)
            continue

        # b) Not merged: inspect if 100% clean and 0 commits ahead of base
        if not is_dirty and commits_ahead == 0:
            if not dry_run:
                _run_git(["worktree", "remove", "--force", entry_path_str], cwd=actual_root)
                _run_git(["branch", "-D", branch], cwd=actual_root)
                if entry.exists():
                    shutil.rmtree(entry, ignore_errors=True)
            summary["clean_pruned"] += 1
            logger.info("Clean subagent worktree pruned: %s (branch: %s)", entry_path_str, branch)
            continue

        # c) Has commits or dirty changes: inspect age
        last_mtime = _get_worktree_mtime(entry_path_str)
        last_commit_time = _get_last_commit_timestamp(entry_path_str)
        last_activity = max(last_mtime, last_commit_time)
        age_seconds = max(0.0, now - last_activity)
        age_hours = round(age_seconds / 3600.0, 2)

        # Get diffstat or status summary for reporting
        diffstat = ""
        if is_dirty:
            diffstat = status_proc.stdout.strip()[:300]
        else:
            base_ref = base_targets[0] if base_targets else "HEAD"
            diff_proc = _run_git(["diff", "--stat", f"{base_ref}..HEAD"], cwd=entry_path_str)
            diffstat = diff_proc.stdout.strip()[:300] if diff_proc.returncode == 0 else ""

        record = {
            "path": entry_path_str,
            "branch": branch,
            "age_hours": age_hours,
            "commits": commits_ahead,
            "dirty": is_dirty,
            "status": diffstat,
        }

        if age_seconds < max_age_seconds:
            summary["preserved_active"].append(record)
        else:
            # Preserve on disk for data safety, record in stale_orphans
            summary["stale_orphans"].append(record)

    return summary
