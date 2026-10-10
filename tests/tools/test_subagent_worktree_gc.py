"""Tests for automatic garbage collection of subagent worktrees."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import tools.subagent_worktree as sw
import tools.subagent_worktree_gc as gc


def _run(cmd: list, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _git(args: list, cwd: Path) -> subprocess.CompletedProcess:
    return _run(["git", *args], cwd)


def _make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "test@test"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "README.md"], repo)
    _git(["commit", "-q", "-m", "initial commit"], repo)
    return repo


class TestSubagentWorktreeGC(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test_subagent_wt_gc_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = _make_repo(self.tmp)

    def test_scenario_1_merged_branch_pruned(self):
        """Cenário 1: Worktree com branch já mergeado no main é removido automaticamente."""
        info = sw.create_subagent_worktree(str(self.repo), "merged-child")
        self.assertIsNotNone(info)
        assert info is not None
        wt_path = Path(info["path"])
        branch = info["branch"]

        # Commit work inside worktree
        (wt_path / "work.txt").write_text("feature content\n", encoding="utf-8")
        _git(["add", "work.txt"], wt_path)
        _git(["config", "user.email", "child@test"], wt_path)
        _git(["config", "user.name", "Child"], wt_path)
        _git(["commit", "-q", "-m", "feature commit"], wt_path)

        # Merge branch into main
        _git(["merge", "--no-ff", "-m", "merge child", branch], self.repo)

        # Run garbage collection
        res = gc.garbage_collect_subagent_worktrees(repo_root=str(self.repo))
        self.assertEqual(res["merged_pruned"], 1)
        self.assertFalse(wt_path.exists())

        # Branch should be deleted
        branch_check = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
        )
        self.assertEqual(branch_check.stdout.strip(), "")

    def test_scenario_2_clean_zero_commits_pruned(self):
        """Cenário 2: Worktree limpo com 0 commits novos é removido automaticamente."""
        info = sw.create_subagent_worktree(str(self.repo), "clean-child")
        self.assertIsNotNone(info)
        assert info is not None
        wt_path = Path(info["path"])
        branch = info["branch"]

        self.assertTrue(wt_path.exists())

        # Run garbage collection
        res = gc.garbage_collect_subagent_worktrees(repo_root=str(self.repo))
        self.assertEqual(res["clean_pruned"], 1)
        self.assertFalse(wt_path.exists())

        # Branch should be deleted
        branch_check = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
        )
        self.assertEqual(branch_check.stdout.strip(), "")

    def test_scenario_3_unmerged_recent_preserved_active(self):
        """Cenário 3: Worktree com commits não-mergeados recente (<72h) é preservado."""
        info = sw.create_subagent_worktree(str(self.repo), "active-child")
        self.assertIsNotNone(info)
        assert info is not None
        wt_path = Path(info["path"])
        branch = info["branch"]

        (wt_path / "wip.txt").write_text("active work\n", encoding="utf-8")
        _git(["add", "wip.txt"], wt_path)
        _git(["config", "user.email", "child@test"], wt_path)
        _git(["config", "user.name", "Child"], wt_path)
        _git(["commit", "-q", "-m", "wip commit"], wt_path)

        res = gc.garbage_collect_subagent_worktrees(repo_root=str(self.repo), max_age_hours=72.0)
        self.assertEqual(res["merged_pruned"], 0)
        self.assertEqual(res["clean_pruned"], 0)
        self.assertEqual(len(res["preserved_active"]), 1)
        self.assertEqual(res["preserved_active"][0]["branch"], branch)
        self.assertEqual(len(res["stale_orphans"]), 0)
        self.assertTrue(wt_path.exists())

    def test_scenario_4_unmerged_stale_preserved_and_reported(self):
        """Cenário 4: Worktree com commits não-mergeados antigo (>=72h) é preservado mas reportado em stale_orphans."""
        info = sw.create_subagent_worktree(str(self.repo), "stale-child")
        self.assertIsNotNone(info)
        assert info is not None
        wt_path = Path(info["path"])
        branch = info["branch"]

        (wt_path / "old.txt").write_text("stale work\n", encoding="utf-8")
        _git(["add", "old.txt"], wt_path)
        _git(["config", "user.email", "child@test"], wt_path)
        _git(["config", "user.name", "Child"], wt_path)
        _git(["commit", "-q", "-m", "old commit"], wt_path)

        # Set commit date to 4 days ago (~96h)
        old_epoch = time.time() - (96 * 3600)
        env = os.environ.copy()
        env["GIT_COMMITTER_DATE"] = str(int(old_epoch))
        env["GIT_AUTHOR_DATE"] = str(int(old_epoch))
        subprocess.run(
            ["git", "commit", "--amend", "--no-edit", f"--date={int(old_epoch)}"],
            cwd=str(wt_path),
            env=env,
            check=True,
            capture_output=True,
        )
        # Also adjust mtime on files and directory
        os.utime(wt_path / "old.txt", (old_epoch, old_epoch))
        os.utime(wt_path, (old_epoch, old_epoch))

        res = gc.garbage_collect_subagent_worktrees(repo_root=str(self.repo), max_age_hours=72.0)
        self.assertEqual(res["merged_pruned"], 0)
        self.assertEqual(res["clean_pruned"], 0)
        self.assertEqual(len(res["preserved_active"]), 0)
        self.assertEqual(len(res["stale_orphans"]), 1)
        orphan = res["stale_orphans"][0]
        self.assertEqual(orphan["branch"], branch)
        self.assertGreaterEqual(orphan["age_hours"], 72.0)
        self.assertGreater(orphan["commits"], 0)
        # Worktree remains safely intact on disk
        self.assertTrue(wt_path.exists())

    def test_dry_run_does_not_delete(self):
        """Dry-run mode lists prune candidates without removing anything."""
        info = sw.create_subagent_worktree(str(self.repo), "dry-child")
        self.assertIsNotNone(info)
        assert info is not None
        wt_path = Path(info["path"])

        res = gc.garbage_collect_subagent_worktrees(repo_root=str(self.repo), dry_run=True)
        self.assertEqual(res["clean_pruned"], 1)
        # Worktree is still on disk
        self.assertTrue(wt_path.exists())


if __name__ == "__main__":
    unittest.main()
