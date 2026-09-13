"""Tests for HAOS Ephemeral Git Worktree Isolation (Orca ADE pattern)."""

import subprocess
import tempfile
from pathlib import Path

from hermes.platform.workspaces.git_worktree import GitWorktreeManager


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "HAOS Tester"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@haos.local"], cwd=path, check=True, capture_output=True)
    # Initial commit
    readme = path / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=path, check=True, capture_output=True)


def test_worktree_lifecycle_and_isolation():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir).resolve()
        _init_git_repo(repo_root)

        mgr = GitWorktreeManager(repo_root=repo_root)
        assert mgr.is_git_repo() is True

        # 1. Create worktree for task T100
        wt_path = mgr.create_worktree(task_id="T100")
        assert wt_path.exists()
        assert (wt_path / ".git").exists()
        assert (repo_root / ".worktrees" / "task-T100").resolve() == wt_path

        # 2. Modify a file inside the isolated worktree
        task_file = wt_path / "task_feature.py"
        task_file.write_text("print('hello from isolated agent')\n", encoding="utf-8")

        # Verify that the main repo DOES NOT have task_feature.py (Strict Isolation)
        assert not (repo_root / "task_feature.py").exists()

        # 3. Check list_worktrees
        wts = mgr.list_worktrees()
        assert len(wts) >= 2  # main repo + T100 worktree
        t100_wt = next((w for w in wts if w.get("task_id") == "T100"), None)
        assert t100_wt is not None
        assert t100_wt["is_clean"] is False  # uncommitted task_feature.py

        # 4. Commit the file inside worktree
        subprocess.run(["git", "add", "task_feature.py"], cwd=wt_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: add task feature"], cwd=wt_path, check=True, capture_output=True)

        assert mgr.is_clean("T100") is True

        # 5. Merge worktree into main branch
        main_branch = mgr.get_current_branch()
        merge_res = mgr.merge_worktree(task_id="T100", target_branch=main_branch)
        assert merge_res["success"] is True

        # File now exists in main repo
        assert (repo_root / "task_feature.py").exists()
        assert (repo_root / "task_feature.py").read_text() == "print('hello from isolated agent')\n"

        # 6. Remove worktree
        removed = mgr.remove_worktree(task_id="T100", force=True, delete_branch=True)
        assert removed is True
        assert not wt_path.exists()
