"""Data dir do engine do plugin de dashboard HAOS (``_haos_engine_dir``).

Esse diretório decide QUAL board o plugin lê. O control plane HAOS usa
``HAOS_DATA_DIR``; enquanto o plugin ignorava a variável, console e control
plane podiam apontar para boards diferentes — a superfície parecia funcionar e
nada era despachado por ela (split-brain silencioso).

O segundo defeito é o symlink pendurado: ``exists()`` é False num link quebrado
(volume desmontado, alvo removido), então o ``symlink()`` seguinte batia em
``FileExistsError`` e o ``except`` silencioso deixava o link morto no lugar, com
o plugin lendo um board inexistente.

O módulo é carregado como o shell oficial carrega (importlib por path +
``exec_module``) e o home é controlado pelo seam REAL: ``_hermes_home_path()``
faz ``from hermes_constants import get_hermes_home`` a cada chamada, logo o patch
vive em ``hermes_constants``. Nada aqui toca ``~/.hermes`` nem ``/root/.haos`` —
o home é sempre um ``tmp_path``.
"""

import importlib.util
import os
from pathlib import Path

import pytest

_PLUGIN_API = (
    Path(__file__).resolve().parents[2] / "plugins" / "haos" / "dashboard" / "plugin_api.py"
)


def _load_plugin_api():
    """Carrega o api exatamente como o shell: importlib por path + exec_module."""
    spec = importlib.util.spec_from_file_location("haos_dashboard_engine_dir", _PLUGIN_API)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin_api():
    return _load_plugin_api()


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Home isolado do teste, ligado ao seam que ``_hermes_home_path()`` consulta."""
    import hermes_constants

    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    # Rede de segurança: se o patch acima não pegasse, o fallback do próprio
    # ``_hermes_home_path()`` lê HERMES_HOME — nunca o home do operador.
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _canonical_board(home: Path) -> Path:
    """Kanban canônico do Hermes no layout que ``_haos_engine_dir()`` prefere."""
    board = home / "kanban" / "boards" / "1" / "kanban.db"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_bytes(b"board canonico do Hermes")
    return board


def test_haos_data_dir_com_kanban_tem_precedencia(plugin_api, hermes_home, tmp_path, monkeypatch):
    """``HAOS_DATA_DIR`` com kanban.db real vence: é o store do control plane."""
    data_dir = tmp_path / "haos_data"
    data_dir.mkdir()
    operator_board = data_dir / "kanban.db"
    operator_board.write_bytes(b"board do control plane")
    # O board do Hermes existe e mesmo assim não pode ganhar.
    assert _canonical_board(hermes_home).is_file()
    monkeypatch.setenv("HAOS_DATA_DIR", str(data_dir))

    result = plugin_api._haos_engine_dir()

    assert result == data_dir
    # Devolve o arquivo do operador, não um symlink para o board do Hermes.
    assert (result / "kanban.db").read_bytes() == b"board do control plane"
    assert not (result / "kanban.db").is_symlink()
    assert result.is_relative_to(tmp_path)
    # Precedência não cria nem toca o engine dir por HERMES_HOME.
    assert not (hermes_home / "haos").exists()


def test_haos_data_dir_sem_kanban_cai_no_home(plugin_api, hermes_home, tmp_path, monkeypatch):
    """Sem kanban.db em ``HAOS_DATA_DIR`` vale o layout por HERMES_HOME."""
    data_dir = tmp_path / "haos_data_vazio"
    data_dir.mkdir()
    canonical = _canonical_board(hermes_home)
    monkeypatch.setenv("HAOS_DATA_DIR", str(data_dir))

    result = plugin_api._haos_engine_dir()

    assert result != data_dir
    assert result == hermes_home / "haos"
    assert result.is_relative_to(tmp_path)
    db = result / "kanban.db"
    assert db.is_symlink()
    assert db.resolve() == canonical.resolve()


def test_symlink_quebrado_e_reparado_para_o_kanban_canonico(plugin_api, hermes_home, monkeypatch):
    """Link pendurado em <home>/haos/kanban.db é removido e recriado."""
    canonical = _canonical_board(hermes_home)
    engine = hermes_home / "haos"
    engine.mkdir()
    dangling = engine / "kanban.db"
    os.symlink(hermes_home / "volume-desmontado" / "kanban.db", dangling)
    assert dangling.is_symlink() and not dangling.exists()
    monkeypatch.delenv("HAOS_DATA_DIR", raising=False)

    result = plugin_api._haos_engine_dir()

    assert result == engine
    assert dangling.is_symlink()  # reparo recria o link, não copia o arquivo
    assert dangling.exists()
    assert dangling.resolve() == canonical.resolve()
    assert os.readlink(dangling) == str(canonical)


def test_symlink_quebrado_sem_canonico_nao_sobrevive(plugin_api, hermes_home, monkeypatch):
    """Sem canônico para apontar, o link morto ainda assim não fica no lugar."""
    engine = hermes_home / "haos"
    engine.mkdir()
    dangling = engine / "kanban.db"
    os.symlink(hermes_home / "volume-desmontado" / "kanban.db", dangling)
    monkeypatch.delenv("HAOS_DATA_DIR", raising=False)

    result = plugin_api._haos_engine_dir()

    assert result == engine
    assert not dangling.is_symlink()
    assert not dangling.exists()
