import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.workspaces.manager import (
    WorkspaceManager, WorkspaceSpec, WorkspaceError,
)
from hermes.platform.adapters.kilo.adapter import KiloLaneAdapter
from hermes.platform.execution.spawn_resolver import SpawnResolver

from hermes_cli import kanban_db as kb


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


class TestK8WorkspaceManager(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env_kanban = os.environ.get("HERMES_KANBAN_HOME")
        self._env_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
        os.environ["HERMES_HOME"] = str(root / "hermes-home")
        self.root = root
        self.db = root / "kanban.db"
        self.adapter = KanbanAdapter(self.db)
        self.manager = WorkspaceManager(db_path=str(self.db))

    def tearDown(self):
        self.adapter.close()
        self.manager.close()
        if self._env_kanban is None:
            os.environ.pop("HERMES_KANBAN_HOME", None)
        else:
            os.environ["HERMES_KANBAN_HOME"] = self._env_kanban
        if self._env_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._env_home
        self._tmp.cleanup()

    def _save(self, spec, **kw):
        return self.adapter.save_task(spec, status="READY", **kw)

    # ------------------------------------------------------------------ #
    def test_create_scratch_lands_on_canonical_workspaces_root(self):
        tid = self._save(TaskSpec(id="T-W1", title="Scratch", goal="g",
                                  workspace_type="scratch"))
        # WorkspaceSpec mantido por compat de API (o card manda).
        path = self.manager.create_workspace("T-W1", WorkspaceSpec(uri="", type="scratch"))
        home = Path(os.environ["HERMES_KANBAN_HOME"])
        self.assertTrue(path.startswith(str(home / "kanban" / "workspaces" / tid)))
        self.assertTrue(Path(path).is_dir())
        # O caminho foi persistido no card canônico.
        self.assertEqual(self.manager.workspace_path(tid), path)

    def test_create_worktree_real_linked_worktree(self):
        # Repo git real + default_workdir do board (anchor canônico).
        repo = self.root / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "k8@test")
        _git(repo, "config", "user.name", "K8 Test")
        (repo / "f.txt").write_text("base\n")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-q", "-m", "base")

        kb.write_board_metadata(None, default_workdir=str(repo))
        tid = self._save(TaskSpec(id="T-WT1", title="Worktree", goal="g",
                                  workspace_type="git_worktree"))
        path = self.manager.create_workspace("T-WT1")
        expected = str(repo / ".worktrees" / tid)
        self.assertTrue(path.startswith(expected), path)

        # É um checkout linked real (não um mkdir avulso).
        head = _git(Path(path), "rev-parse", "--git-dir")
        self.assertNotEqual(head.stdout.strip(), ".git")
        out = _git(repo, "worktree", "list")
        self.assertIn(path, out.stdout)
        # Branch determinística do card.
        branch = _git(Path(path), "branch", "--show-current").stdout.strip()
        self.assertEqual(branch, f"wt/{tid}")

    def test_cleanup_removes_only_managed_scratch(self):
        tid = self._save(TaskSpec(id="T-W2", title="Clean", goal="g",
                                  workspace_type="scratch"))
        path = self.manager.create_workspace(tid)
        self.assertTrue(Path(path).is_dir())

        # Descendente do root gerenciado -> remove.
        self.assertTrue(self.manager.cleanup_workspace(path))
        self.assertFalse(Path(path).exists())

        # Fora do root gerenciado -> no-op seguro (contenção #28818).
        foreign = self.root / "user-data"
        foreign.mkdir()
        self.assertFalse(self.manager.cleanup_workspace(str(foreign)))
        self.assertTrue(foreign.exists())

    def test_unknown_task_raises(self):
        with self.assertRaises(WorkspaceError):
            self.manager.create_workspace("T-NOPE")


class TestK8KiloLaneAdapterCanonical(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env_kanban = os.environ.get("HERMES_KANBAN_HOME")
        os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
        self.db = root / "kanban.db"
        self.adapter = KanbanAdapter(self.db)

    def tearDown(self):
        self.adapter.close()
        if self._env_kanban is None:
            os.environ.pop("HERMES_KANBAN_HOME", None)
        else:
            os.environ["HERMES_KANBAN_HOME"] = self._env_kanban
        self._tmp.cleanup()

    def test_kilo_adapter_resolves_canonical_workspace(self):
        spec = TaskSpec(id="T-3", title="Kilo Task", goal="Coding",
                        required_capabilities=["git"], workspace_type="scratch")
        tid = self.adapter.save_task(spec, status="READY")
        assignment = SpawnResolver().resolve(spec)

        adapter = KiloLaneAdapter(db_path=str(self.db))
        res = adapter.execute_assignment(assignment)

        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["lane"], "kilo")
        home = Path(os.environ["HERMES_KANBAN_HOME"])
        self.assertTrue(res["workspace_path"].startswith(str(home / "kanban" / "workspaces" / tid)))
        # Sem naming de stub /tmp avulso.
        self.assertNotIn("haos_workspace_", res["workspace_path"])
        # O card canônico carrega o caminho resolvido.
        card = self.adapter.get_task(tid)
        self.assertEqual(card["spec"].get("id"), "T-3")

    def test_kilo_adapter_requires_saved_card(self):
        spec = TaskSpec(id="T-UNSAVED", title="Ghost", goal="g",
                        workspace_type="scratch")
        assignment = SpawnResolver().resolve(spec)
        adapter = KiloLaneAdapter(db_path=str(self.db))
        with self.assertRaises(WorkspaceError):
            adapter.execute_assignment(assignment)


if __name__ == "__main__":
    unittest.main()
