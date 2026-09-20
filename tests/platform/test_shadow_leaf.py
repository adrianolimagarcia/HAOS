import sys
import shutil
import tempfile
import time
from pathlib import Path
import subprocess

import pytest

sys.path.insert(0, ".")
from hermes.platform.shadow_leaf import ShadowLeafManager, ShadowLeaf


@pytest.fixture
def temp_git_repo(tmp_path):
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "HAOS Tester"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@haos.ai"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


def test_shadow_leaf_background_execution(temp_git_repo, tmp_path):
    shadows_root = tmp_path / "shadows"
    mgr = ShadowLeafManager(base_repo_dir=temp_git_repo, shadows_root=shadows_root)

    # Função de subagente customizado executando trabalho em paralelo
    def background_subagent_task(leaf: ShadowLeaf):
        time.sleep(0.2)
        # O subagente trabalha isolado dentro do seu próprio worktree
        wt = Path(leaf.worktree_path)
        (wt / "parallel_output.json").write_text('{"result": "parallel success"}', encoding="utf-8")
        leaf.summary = "Trabalho paralelo finalizado com sucesso no worktree."

    # 1. Spawn da Sombra em Background (libera o bot pai imediatamente)
    leaf = mgr.spawn_shadow(
        parent_bot_id="forge_coder",
        parent_bot_name="Forge Coder",
        profile="forge_coder",
        task_description="Processar dados em paralelo sem concorrer com main",
        executor_fn=background_subagent_task,
        run_in_background=True,
    )

    # O bot titular é liberado imediatamente!
    assert leaf.leaf_id.startswith("shadow-forge_coder-")
    assert leaf.status in ("active", "completed")
    
    # Aguarda o subagente em background finalizar
    for _ in range(20):
        if leaf.status == "completed":
            break
        time.sleep(0.05)

    assert leaf.status == "completed"
    assert (Path(leaf.worktree_path) / "parallel_output.json").exists()
    assert not (temp_git_repo / "parallel_output.json").exists(), "Zero concorrência: main intacto!"

    # 2. Descarte após conclusão
    ok = mgr.discard_shadow(leaf.leaf_id)
    assert ok is True
    assert not Path(leaf.worktree_path).exists()
