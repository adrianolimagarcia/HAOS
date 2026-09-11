"""Contrato do fork HAOS: nenhum resolver de home pode cair em ~/.hermes.

Complementa ``test_haos_default_home.py`` (que cobre o default de plataforma):
aqui o contrato é por CALL-SITE — cada resolver que um usuário alcança sem env
(cron externo, python cru, unidade systemd) tem de devolver o home canônico do
fork. O default antigo ``~/.hermes`` criava store órfão (split-brain).

Testes de comportamento: chamam o resolver de verdade, nunca leem o fonte.
"""
from pathlib import Path

import pytest


@pytest.fixture
def sem_env(monkeypatch, tmp_path):
    """HOME temporário sem HERMES_HOME/HAOS_HOME — o cenário do processo 'cru'.

    HOME é setado junto com Path.home(): parte dos resolvers usa
    ``os.path.expanduser("~")`` (que lê $HOME) e parte usa ``Path.home()``.
    """
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HAOS_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_resolvers_de_runtime_sem_env_usam_home_canonico(sem_env):
    """Cada resolver alcançável sem env aponta para <home>/.haos."""
    from hermes_cli._startup_fast import _default_home as startup_fast_home
    from hermes_cli.dashboard_procs import _hermes_home_dir
    from hermes.platform.security.workspace_scope import resolve_workspace_scope
    from plugins.memory.mem0._oss_providers import _default_qdrant_path
    from tools.bot_mode_dm import _default_home as bot_dm_home
    from tools.bot_mode_probe import _default_home as bot_probe_home
    from tui_gateway.methods_bot_relay import _relay_root

    canonico = sem_env / ".haos"

    assert _hermes_home_dir() == canonico
    assert resolve_workspace_scope(project_dir=sem_env).agent_workspace == canonico
    assert Path(startup_fast_home()) == canonico
    assert Path(bot_dm_home()) == canonico
    assert Path(bot_probe_home()) == canonico
    assert Path(_default_qdrant_path()).parent == canonico
    assert _relay_root() == canonico


def test_workspace_scope_nao_degrada_para_hermes(sem_env):
    """O boundary de segurança do agent workspace nunca aponta para o store legado."""
    from hermes.platform.security.workspace_scope import resolve_workspace_scope

    scope = resolve_workspace_scope(project_dir=sem_env)

    assert scope.agent_workspace == sem_env / ".haos"
    assert ".hermes" not in str(scope.agent_workspace)


def test_remap_de_unidade_acompanha_a_baseline_que_casou(monkeypatch, tmp_path):
    """Remap sob sudo: home canônico vira canônico do alvo, legado vira legado, custom fica.

    Um alvo fixo (sempre .haos) apontaria a unidade de um home legado para um store
    inexistente; um baseline fixo em ~/.hermes deixaria de remapear o home do fork.
    """
    from hermes_cli.gateway import _hermes_home_for_target_user

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HAOS_HOME", raising=False)

    casos = {
        None: "/home/alice/.haos",
        str(tmp_path / ".haos"): "/home/alice/.haos",
        str(tmp_path / ".haos" / "profiles" / "coder"): "/home/alice/.haos/profiles/coder",
        str(tmp_path / ".hermes"): "/home/alice/.hermes",
        str(tmp_path / ".hermes" / "profiles" / "coder"): "/home/alice/.hermes/profiles/coder",
        "/opt/data": "/opt/data",
    }
    for env, esperado in casos.items():
        if env is None:
            monkeypatch.delenv("HERMES_HOME", raising=False)
        else:
            monkeypatch.setenv("HERMES_HOME", env)
        assert _hermes_home_for_target_user("/home/alice") == esperado, env


def test_seat_belt_do_auth_protege_o_store_real_e_libera_home_temporario(monkeypatch, tmp_path):
    """O seat-belt do auth.json tem de proteger o store REAL do fork, sem travar a suíte.

    Antes ele comparava só com ``~/.hermes/auth.json``: com o home canônico em
    ``~/.haos`` o store real ficava desprotegido — e comparar com o root resolvido
    do ambiente dispararia em qualquer teste com HERMES_HOME temporário.
    """
    from hermes_cli import auth

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HAOS_HOME", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "teste")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert auth._auth_file_path() == tmp_path / "auth.json"  # isolado: passa

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".haos"))
    with pytest.raises(RuntimeError, match="real user auth store"):
        auth._auth_file_path()
