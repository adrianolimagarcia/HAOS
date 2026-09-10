"""Tests for GitWorktreeManager and AutoMergeGate."""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hermes.platform.workspaces.automerge import AutoMergeGate
from hermes.platform.workspaces.git_worktree import GitWorktreeManager


class TestGitWorktreeAndAutoMerge(unittest.TestCase):
    def setUp(self):
        self.tmp_repo = Path("/tmp/fake_repo")
        self.manager = GitWorktreeManager(repo_root=self.tmp_repo)

    @patch.object(GitWorktreeManager, "_run_git")
    def test_create_worktree(self, mock_run_git):
        # A produção decide entre criar o branch e reatá-lo sondando
        # ``rev-parse --verify`` e tratando exceção como "não existe". Um mock que
        # devolve string para TODA chamada faz a sonda parecer bem-sucedida e o
        # teste passa a medir o caminho de reattach — por isso a sonda levanta aqui.
        def run_git(*args, **kwargs):
            if args[:2] == ("rev-parse", "--verify"):
                raise subprocess.CalledProcessError(1, args)
            return "Preparing worktree"

        mock_run_git.side_effect = run_git
        worktree_path = self.manager.create_worktree("task-123", base_branch="haos-fork")

        self.assertEqual(worktree_path.name, "task-task-123")
        mock_run_git.assert_called_with(
            "worktree", "add", "-b", "haos/task-task-123", str(worktree_path), "haos-fork"
        )

    @patch.object(GitWorktreeManager, "_run_git")
    def test_create_worktree_reattaches_an_existing_branch(self, mock_run_git):
        # Branch já existente: reatar em vez de ``-b`` — é o que permite reusar a
        # lane de um task que já rodou em vez de morrer no "branch already exists".
        # Aqui o mock devolve string para TODA chamada, então a sonda
        # ``rev-parse --verify`` parece bem-sucedida: é exatamente o caminho testado.
        mock_run_git.return_value = "Preparing worktree"
        worktree_path = self.manager.create_worktree("task-123", base_branch="haos-fork")

        mock_run_git.assert_called_with(
            "worktree", "add", str(worktree_path), "haos/task-task-123"
        )

    @patch.object(GitWorktreeManager, "_run_git")
    def test_get_diff(self, mock_run_git):
        mock_run_git.return_value = "diff --git a/file b/file"
        diff = self.manager.get_diff("task-123", base_branch="haos-fork")
        self.assertIn("diff --git", diff)
        # ``check=False``: diff contra ref ausente devolve vazio, não levanta.
        mock_run_git.assert_called_with("diff", "haos-fork...haos/task-task-123", check=False)

    @patch.object(AutoMergeGate, "run_tests_in_worktree")
    @patch.object(GitWorktreeManager, "merge_worktree")
    @patch.object(GitWorktreeManager, "remove_worktree")
    def test_automerge_gate_success(self, mock_remove, mock_merge, mock_tests):
        mock_tests.return_value = {"success": True, "returncode": 0, "stdout_tail": "All tests passed"}
        mock_merge.return_value = "Merge made by recursive strategy"
        mock_remove.return_value = True

        gate = AutoMergeGate(self.manager)
        res = gate.verify_and_merge("task-123", target_branch="haos-fork")

        self.assertTrue(res["merged"])
        mock_merge.assert_called_once_with("task-123", target_branch="haos-fork")
        mock_remove.assert_called_once_with("task-123")

    @patch.object(AutoMergeGate, "run_tests_in_worktree")
    def test_automerge_gate_blocks_on_failing_tests(self, mock_tests):
        mock_tests.return_value = {"success": False, "returncode": 1, "stderr_tail": "AssertionError"}

        gate = AutoMergeGate(self.manager)
        res = gate.verify_and_merge("task-123", target_branch="haos-fork")

        self.assertFalse(res["merged"])
        self.assertIn("Test verification failed", res["reason"])


if __name__ == "__main__":
    unittest.main()
