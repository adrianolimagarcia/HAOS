"""`cron.restart_safe_scope`: contrato do handoff restart-safe do worker de cron.

O defeito que estes testes prendem: num nó onde `systemd-run --user --scope` é
permanentemente inalcançável (systemd sem `pam_systemd`, então `systemd --user` nunca
sobe), o default `require` faz TODO job agendado falhar sem nunca rodar. `prefer`
degrada — mas degrada para um worker EXTERNO com o handoff #101940 (separação de
processo preservada, isolação de cgroup perdida), nunca para o caminho in-process:
rodar o job dentro do gateway bloqueia o processo e também morre no restart.
"""

from __future__ import annotations

import pytest

from cron import scheduler
from tools import process_registry

_SCOPE_UNAVAILABLE = "cannot create restart-safe systemd scope for gateway child"


class _HandoffReached(Exception):
    """Sentinela: o caminho degradado chegou ao handoff externo (não voltou in-process)."""


def _raise_scope_unavailable(command, *, unit_suffix, require_restart_safe_scope=False):
    raise RuntimeError(f"{_SCOPE_UNAVAILABLE}: systemd-run --user --scope is unavailable")


def _job() -> dict:
    return {"id": "job-under-test", "execution_id": "exec-under-test"}


def _config(monkeypatch, *, policy: str = "prefer", upstream_require=None):
    """Patch dos DOIS leitores: `load_config` (política do fork) e `load_config_readonly`
    (chave do upstream). A chave do upstream existe sempre no DEFAULT_CONFIG — o que
    decide é o valor, então `upstream_require=None` a omite do cenário."""
    monkeypatch.setattr(scheduler, "load_config", lambda: {"cron": {"restart_safe_scope": policy}})
    cron_cfg: dict = {}
    if upstream_require is not None:
        cron_cfg["require_restart_safe_scope"] = upstream_require
    monkeypatch.setattr(scheduler, "load_config_readonly", lambda: {"cron": cron_cfg})


def test_policy_defaults_to_require_and_ignores_unknown_values(monkeypatch):
    """O default preserva o contrato do upstream; valor inválido não vira degradação."""
    monkeypatch.setattr(scheduler, "load_config", lambda: None)
    assert scheduler._restart_safe_scope_policy() == "require"

    monkeypatch.setattr(scheduler, "load_config", lambda: {"cron": {}})
    assert scheduler._restart_safe_scope_policy() == "require"

    monkeypatch.setattr(scheduler, "load_config", lambda: {"cron": {"restart_safe_scope": "talvez"}})
    assert scheduler._restart_safe_scope_policy() == "require"

    monkeypatch.setattr(
        scheduler, "load_config", lambda: {"cron": {"restart_safe_scope": "  PREFER "}}
    )
    assert scheduler._restart_safe_scope_policy() == "prefer"


def test_policy_falls_back_to_require_when_config_is_unreadable(monkeypatch):
    def boom():
        raise OSError("config ilegível")

    monkeypatch.setattr(scheduler, "load_config", boom)
    assert scheduler._restart_safe_scope_policy() == "require"


def test_require_reports_scope_failure(monkeypatch):
    """`require`: sem scope o run FALHA — não cai para in-process em silêncio."""
    monkeypatch.setattr(process_registry, "restart_safe_gateway_child_argv", _raise_scope_unavailable)
    _config(monkeypatch, policy="require")

    with pytest.raises(RuntimeError, match="systemd-run --user --scope is unavailable"):
        scheduler._launch_external_cron_worker(_job())


def test_prefer_degrades_to_the_external_worker_path(monkeypatch):
    """`prefer`: sem scope o job SEGUE externo — chega ao handoff #101940, nunca in-process.

    Rodar o job dentro do gateway bloquearia o processo e também morreria no restart; a
    separação de processo é o que o upstream exige mesmo quando a isolação de cgroup se perde
    (`GatewayChildDispatch("degraded")`: o caller TEM de lançar o subprocesso externo).
    """
    monkeypatch.setattr(process_registry, "restart_safe_gateway_child_argv", _raise_scope_unavailable)
    _config(monkeypatch, policy="prefer")

    def _reached_the_handoff(execution_id):
        raise _HandoffReached(execution_id)

    monkeypatch.setattr(scheduler, "mark_execution_handoff_pending", _reached_the_handoff)

    with pytest.raises(_HandoffReached, match="exec-under-test"):
        scheduler._launch_external_cron_worker(_job())


def test_in_process_path_is_the_dispatchers_call_not_the_policy(monkeypatch):
    """Fora de um gateway gerenciado o dispatcher devolve `in_process` e o job roda no processo."""
    monkeypatch.setattr(
        process_registry,
        "restart_safe_gateway_child_argv",
        lambda command, *, unit_suffix, require_restart_safe_scope=False: (
            process_registry.GatewayChildDispatch("in_process", command)
        ),
    )
    _config(monkeypatch, policy="prefer")

    assert scheduler._launch_external_cron_worker(_job()) is False


def test_upstream_require_key_is_fail_closed_even_under_the_fork_prefer_policy(monkeypatch):
    """`cron.require_restart_safe_scope: true` (chave do upstream) é fail-closed mesmo com a
    política `prefer` do fork declarada pelo appliance: degradar em silêncio contradiria o
    pedido explícito de restart-safety."""
    monkeypatch.setattr(process_registry, "restart_safe_gateway_child_argv", _raise_scope_unavailable)
    _config(monkeypatch, policy="prefer", upstream_require=True)

    with pytest.raises(RuntimeError, match="systemd-run --user --scope is unavailable"):
        scheduler._launch_external_cron_worker(_job())


def test_upstream_false_key_does_not_disable_the_fork_policy(monkeypatch):
    """A chave do upstream em False é o default do DEFAULT_CONFIG — presença não é pedido:
    a política `prefer` do fork continua degradando (e não fail-closed)."""
    monkeypatch.setattr(process_registry, "restart_safe_gateway_child_argv", _raise_scope_unavailable)
    _config(monkeypatch, policy="prefer", upstream_require=False)

    def _reached_the_handoff(execution_id):
        raise _HandoffReached(execution_id)

    monkeypatch.setattr(scheduler, "mark_execution_handoff_pending", _reached_the_handoff)

    with pytest.raises(_HandoffReached, match="exec-under-test"):
        scheduler._launch_external_cron_worker(_job())
