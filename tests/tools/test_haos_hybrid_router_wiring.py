"""Wiring do router híbrido em produção (tools/haos_memory_tools._get_hybrid_router).

Fixa os dois gates do commit de pilotagem:
1. RAPTOR entra por DISPONIBILIDADE de arquivo (fail-closed, como GraphRAG
   available()): sem raptor.db o router é construído sem store e o caminho de
   leitura NUNCA cria o DB; com raptor.db, o store é passado ao router.
2. Planner é opt-in via config `memory.hybrid_planner` (default False =
   cascata fixa, comportamento original byte-for-byte).
"""
from __future__ import annotations

import pytest

import hermes_cli.config as hc
import tools.haos_memory_tools as hmt


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    (home / "memory").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    hc._LOAD_CONFIG_CACHE.clear()
    yield home
    hc._LOAD_CONFIG_CACHE.clear()


def _set_planner(home, enabled: bool) -> None:
    (home / "config.yaml").write_text(f"memory:\n  hybrid_planner: {str(enabled).lower()}\n")
    hc._LOAD_CONFIG_CACHE.clear()


def test_sem_raptor_db_router_ve_nada_e_nao_cria_db(hermes_home):
    router = hmt._get_hybrid_router()
    assert router.raptor_store is None
    assert router.use_planner is False
    # fail-closed: o caminho de leitura não pode materializar o DB
    assert not (hermes_home / "memory" / "raptor.db").exists()


def test_raptor_db_presente_entra_no_router(hermes_home):
    from hermes.platform.memory.raptor_memory import RaptorStore
    RaptorStore(db_path=hermes_home / "memory" / "raptor.db")  # simula o build
    router = hmt._get_hybrid_router()
    assert router.raptor_store is not None
    assert router.raptor_store.db_path == hermes_home / "memory" / "raptor.db"


def test_planner_opt_in_via_config(hermes_home):
    _set_planner(hermes_home, True)
    router = hmt._get_hybrid_router()
    assert router.use_planner is True
    _set_planner(hermes_home, False)
    router = hmt._get_hybrid_router()
    assert router.use_planner is False
