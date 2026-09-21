"""Resolução do data dir do HAOS pelo bridge de delegação de subagente.

Regressão: a cadeia de candidatos aceitava ``kanban.db`` pelo simples
``.exists()``, então o stub de 0 bytes em ``/tmp/haos_shared_data`` vencia o
board real em qualquer processo sem ``HAOS_DATA_DIR`` (o gateway não seta essa
env). As delegações eram gravadas num board que ninguém lê.

Isolamento: nada aqui toca ``~/.haos``, ``~/.hermes`` ou ``/tmp/haos_shared_data``
de verdade — ``Path.home()``, ``HERMES_HOME``/``HAOS_HOME`` e o seam canônico
``hermes_constants.get_hermes_home`` são todos redirecionados para ``tmp_path``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import hermes_constants
import pytest
from hermes.platform.tasks import haos_delegation_bridge as bridge


def _board(root: Path, name: str) -> Path:
    """Candidato com um kanban.db SQLite de verdade (não-vazio)."""
    data_dir = root / name
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data_dir / "kanban.db"))
    try:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT)")
        conn.commit()
    finally:
        conn.close()
    return data_dir


def _stub(root: Path, name: str) -> Path:
    """Candidato com o kanban.db de 0 bytes que causava o split-brain."""
    data_dir = root / name
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "kanban.db").write_bytes(b"")
    return data_dir


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Redireciona todo candidato para tmp_path, incluindo o home canônico.

    O seam real é ``hermes_constants.get_hermes_home``: o bridge o importa
    dentro da função, então é a definição em ``hermes_constants`` que a
    produção lê em tempo de chamada.

    Limpa ``HAOS_DATA_DIR``/``HERMES_HOME``/``HAOS_HOME`` (podem vir do host),
    então cada teste chama isto ANTES de setar a env que quer exercitar.
    """
    operator_home = tmp_path / "operator_home"
    operator_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: operator_home)
    monkeypatch.delenv("HAOS_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HAOS_DATA_DIR", raising=False)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)


def test_kanban_db_vazio_nao_vence_board_real(tmp_path, monkeypatch):
    """(a) Um kanban.db de 0 bytes é candidato INVÁLIDO, mesmo via HAOS_DATA_DIR."""
    home = tmp_path / "hermes_home"
    canonical = _board(home, "haos")
    _isolate(tmp_path, monkeypatch, home)

    stub_dir = _stub(tmp_path, "haos_data_dir_stub")
    monkeypatch.setenv("HAOS_DATA_DIR", str(stub_dir))

    result = bridge._get_haos_data_dir()

    assert result != stub_dir, "o stub vazio de HAOS_DATA_DIR não é um board"
    assert result == canonical


def test_haos_data_dir_valido_vence_tudo(tmp_path, monkeypatch):
    """(b) HAOS_DATA_DIR explícito e válido tem precedência sobre todo o resto."""
    home = tmp_path / "hermes_home"
    _board(home, "haos")  # home canônico também válido: não pode roubar a vez
    _isolate(tmp_path, monkeypatch, home)

    env_dir = _board(tmp_path, "env_haos")
    monkeypatch.setenv("HAOS_DATA_DIR", str(env_dir))

    assert bridge._get_haos_data_dir() == env_dir


def test_home_canonico_vence_store_legado_de_tmp(tmp_path, monkeypatch):
    """(c) Sem HAOS_DATA_DIR, o engine dir canônico vem antes de /tmp."""
    home = tmp_path / "hermes_home"
    canonical = _board(home, "haos")
    _isolate(tmp_path, monkeypatch, home)
    monkeypatch.setenv("HERMES_HOME", str(home))

    result = bridge._get_haos_data_dir()

    assert result == canonical
    assert result != Path("/tmp/haos_shared_data")


def test_kanban_db_symlinkado_conta_como_board(tmp_path, monkeypatch):
    """O engine dir canônico symlinka kanban.db — o symlink é board válido.

    ``_haos_engine_dir`` (plugin do dashboard) cria ``<home>/haos/kanban.db``
    como symlink para o board canônico. Exigir arquivo regular quebraria
    justamente o caminho que tira a delegação do stub vazio de /tmp.
    """
    real_board = _board(tmp_path, "board_real")
    home = tmp_path / "hermes_home"
    engine = home / "haos"
    engine.mkdir(parents=True)
    (engine / "kanban.db").symlink_to(real_board / "kanban.db")
    _isolate(tmp_path, monkeypatch, home)

    assert bridge._get_haos_data_dir() == engine
