"""Contrato do populador da memória canônica (scripts/haos_memory_populate.py).

Prova os dois defeitos que a verificação do appliance expôs:
  * o home tem de ser o store do nó (sem env o runtime cai em ~/.hermes);
  * o store GraphRAG só é escrito pelo write-through `store=` do updater — sem
    ele o adapter fica só em memória e graphrag.db nunca recebe entidades.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "haos_memory_populate.py"


def _load_populate_module():
    spec = importlib.util.spec_from_file_location("haos_memory_populate", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def node_home(tmp_path, monkeypatch):
    """Home do nó com vault canônico de duas notas (uma delas com ADR)."""
    home = tmp_path / ".haos"
    vault = home / "obsidian_vault"
    (vault / "adrs").mkdir(parents=True)
    (vault / "index.md").write_text(
        "# HAOS Knowledge Vault\n\nVault canônico do nó.\n", encoding="utf-8"
    )
    (vault / "adrs" / "ADR-001-haos.md").write_text(
        "# Seed ADR - HAOS\n\nstatus: accepted\n\nO nó usa Sqlite para o estado.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HAOS_HOME", str(home))
    return home


def test_indexa_vault_no_store_deepdoc(node_home):
    """Passo 1: as notas viram chunks no ragflow.db do home do nó."""
    module = _load_populate_module()

    result = module.index_vault_rag(node_home)

    assert result["status"] == "ok", result
    assert result["files"] == 2, result
    assert result["chunks"] >= 2, result
    assert Path(result["db"]) == node_home / "memory" / "ragflow.db"


def test_grafa_vault_no_store_canonico(node_home):
    """Passo 2: o store canônico recebe entidades e relações de verdade."""
    module = _load_populate_module()

    result = module.build_graphrag_store(node_home)

    assert result["status"] == "ok", result
    assert result["notes"] == 2, result
    # Sem o write-through `store=` no updater isto é 0/0: o adapter fica só em memória.
    assert result["entities"] >= 2, result
    assert result["relations"] >= 1, result
    assert Path(result["store"]) == node_home / "memory" / "graphrag.db"


def test_vault_ausente_e_pulado_sem_criar_store(tmp_path, monkeypatch):
    """Sem vault o passo é pulado e não inventa um store vazio."""
    home = tmp_path / ".haos"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _load_populate_module()

    result = module.build_graphrag_store(home)

    assert result["status"] == "skipped", result
    assert not (home / "memory" / "graphrag.db").exists()


def test_home_resolve_env_antes_do_default(tmp_path, monkeypatch):
    """HAOS_HOME/HERMES_HOME vencem o fallback ~/.haos (evita o store órfão)."""
    home = tmp_path / "no"
    monkeypatch.setenv("HAOS_HOME", str(home))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    module = _load_populate_module()

    assert module.resolve_home(None) == home.resolve()
