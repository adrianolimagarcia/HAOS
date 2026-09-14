"""``haos worktree`` — audit (``list [--json] [--older-than DAYS]``) and reclaim
(``prune [--dry-run] [--json] [--older-than DAYS] [--trees-only | --branches-only]``)
accumulated git worktrees/branches. ``--json`` output is the only thing written to stdout in
that mode so scripts can consume it."""

from __future__ import annotations
from hermes_constants import product_command

import json
from dataclasses import asdict
from typing import Optional


def _fmt_size(size_mb: Optional[int]) -> str:
    if size_mb is None:
        return "?"
    return f"{size_mb / 1024:.1f}G" if size_mb >= 1024 else f"{size_mb}M"


def _list(worktree_gc, repo_root: str, args) -> int:
    older_than = getattr(args, "older_than", None)
    records = worktree_gc.audit_worktrees(repo_root, older_than_days=older_than)
    external = worktree_gc.audit_external_trees(repo_root)
    branch_records = worktree_gc.audit_branches(repo_root)
    if getattr(args, "json", False):
        print(json.dumps({
            "repo": repo_root,
            "trees": [asdict(r) for r in records],
            "external_trees": [asdict(r) for r in external],
            "branches": [asdict(b) for b in branch_records],
        }, indent=2))
        return 0
    if not records and not external:
        print("No worktrees under .worktrees/ — nothing to reclaim.")
        return 0
    if records:
        total_mb = sum(r.size_mb or 0 for r in records)
        reapable_mb = sum(r.size_mb or 0 for r in records if r.verdict.startswith("reap"))
        print(f"{'TREE':32} {'AGE':>6} {'SIZE':>6} {'VERDICT':13} REASON")
        for r in sorted(records, key=lambda x: -(x.size_mb or 0)):
            print(f"{r.name[:32]:32} {r.age_days:>5.1f}d {_fmt_size(r.size_mb):>6} {r.verdict:13} {r.reason}")
        print(
            f"\n{len(records)} tree(s), {_fmt_size(total_mb)} total — "
            f"{_fmt_size(reapable_mb)} reclaimable now via `{product_command('worktree')} prune`.")
    if external:
        print(f"\n{len(external)} externally-registered worktree(s) (never touched by prune):")
        for e in external:
            state = "MISSING" if e.missing else ("locked" if e.locked else "ok")
            print(f"  {e.path}  [{e.branch or '?'}]  {state}")
        if any(e.missing and not e.locked for e in external):
            print(
                f"  Stale registrations (MISSING) are cleaned by "
                f"`{product_command('worktree')} prune` (metadata only)."
            )
    deletable = [b for b in branch_records if b.verdict == "delete"]
    if deletable:
        print(f"{len(deletable)} local branch(es) fully merged/patch-equivalent upstream would also be deleted.")
    return 0


def _prune(worktree_gc, repo_root: str, args) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    as_json = bool(getattr(args, "json", False))
    older_than = getattr(args, "older_than", None)
    actions: list = []
    kept: list = []
    if not getattr(args, "branches_only", False):
        actions += worktree_gc.prune_missing_registrations(repo_root, dry_run=dry_run)
        tree_records = worktree_gc.audit_worktrees(repo_root, with_sizes=False, older_than_days=older_than)
        actions += worktree_gc.reclaim_worktrees(repo_root, dry_run=dry_run, records=tree_records)
        kept = [r for r in tree_records if r.verdict == "keep"
                and "kanban" not in r.reason and "in use" not in r.reason]
        if kept and not as_json:
            print(f"Preserved {len(kept)} tree(s) with real work:")
            for r in kept:
                print(f"  {r.name}: {r.reason}")
    if not getattr(args, "trees_only", False):
        actions += worktree_gc.reclaim_branches(repo_root, dry_run=dry_run)

    if as_json:
        print(json.dumps({
            "repo": repo_root,
            "dry_run": dry_run,
            "actions": actions,
            "preserved": [asdict(r) for r in kept],
        }, indent=2))
        return 0
    if actions:
        for line in actions:
            print(f"  {line}")
        print(f"{len(actions)} action(s) {'planned' if dry_run else 'done'}.")
    else:
        print("Nothing to reclaim — all trees/branches carry real work or are in use.")
    return 0


def _create(worktree_gc, repo_root: str, args) -> int:
    task_id = getattr(args, "task_id", None)
    if not task_id:
        print("Error: task_id is required for create (e.g. haos worktree create T-101)")
        return 1
    from pathlib import Path
    from hermes.platform.workspaces.git_worktree import GitWorktreeManager
    mgr = GitWorktreeManager(repo_root=Path(repo_root))
    base_branch = getattr(args, "base", None)
    try:
        wt_path = mgr.create_worktree(task_id=task_id, base_branch=base_branch)
        print("✓ Worktree created successfully:")
        print(f"  • Path:   {wt_path}")
        print(f"  • Branch: haos/task-{task_id}")
        return 0
    except Exception as exc:
        print(f"✗ Failed to create worktree: {exc}")
        return 1


def _remove(worktree_gc, repo_root: str, args) -> int:
    task_id = getattr(args, "task_id", None)
    if not task_id:
        print("Error: task_id is required for remove")
        return 1
    from pathlib import Path
    from hermes.platform.workspaces.git_worktree import GitWorktreeManager
    mgr = GitWorktreeManager(repo_root=Path(repo_root))
    force = bool(getattr(args, "force", True))
    delete_branch = bool(getattr(args, "delete_branch", False))
    removed = mgr.remove_worktree(task_id=task_id, force=force, delete_branch=delete_branch)
    if removed:
        print(f"✓ Worktree task-{task_id} removed.")
        return 0
    else:
        print(f"✗ Worktree task-{task_id} not found.")
        return 1


def _merge(worktree_gc, repo_root: str, args) -> int:
    task_id = getattr(args, "task_id", None)
    if not task_id:
        print("Error: task_id is required for merge")
        return 1
    from pathlib import Path
    from hermes.platform.workspaces.git_worktree import GitWorktreeManager
    mgr = GitWorktreeManager(repo_root=Path(repo_root))
    target = getattr(args, "target", None)
    squash = bool(getattr(args, "squash", False))
    res = mgr.merge_worktree(task_id=task_id, target_branch=target, squash=squash)
    if res.get("success"):
        print(f"✓ Worktree task-{task_id} merged into {res.get('target_branch')}:")
        if res.get("output"):
            print(f"  {res['output']}")
        return 0
    else:
        print(f"✗ Merge failed: {res.get('error')}")
        return 1


_ACTIONS = {
    "list": _list,
    "prune": _prune,
    "create": _create,
    "remove": _remove,
    "merge": _merge,
}


def cmd_worktree(args) -> int:
    from hermes_cli import worktree_gc
    repo_root = getattr(args, "repo", None)
    if not repo_root:
        import cli as _cli
        repo_root = _cli._git_repo_root()
    if not repo_root:
        print("Not inside a git repository (or pass --repo <path>).")
        return 1
    older_than = getattr(args, "older_than", None)
    if older_than is not None and older_than < 0:
        print("--older-than must be a non-negative number of days.")
        return 1
    action = getattr(args, "worktree_action", None) or "list"
    handler = _ACTIONS.get(action)
    if handler is None:
        print(f"Unknown worktree action: {action}")
        return 1
    return handler(worktree_gc, repo_root, args)
