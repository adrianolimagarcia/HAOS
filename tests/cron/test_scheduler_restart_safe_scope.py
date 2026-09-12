"""`cron.restart_safe_scope`: contrato do handoff restart-safe do worker de cron.

O defeito que estes testes prendem: num nó onde `systemd-run --user --scope` é
permanentemente inalcançável (systemd sem `pam_systemd`, então `systemd --user` nunca
sobe), o default `require` faz TODO job agendado falhar sem nunca rodar. `prefer`
degrada para o caminho in-process — é o que faz o job existir no appliance.
"""

from __future__ import annotations

import pytest

from cron import scheduler
from tools import process_registry

_SCOPE_UNAVAILABLE = "cannot create restart-safe systemd scope for gateway child"


def _raise_scope_unavailable(command, *, unit_suffix, require_restart_safe_scope=False):
    raise RuntimeError(f"{_SCOPE_UNAVAILABLE}: systemd-run --user --scope is unavailable")


def _job() -> dict:
    return {"id": "job-under-test", "execution_id": "exec-under-test"}


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
    monkeypatch.setattr(scheduler, "load_config", lambda: {"cron": {"restart_safe_scope": "require"}})

    with pytest.raises(RuntimeError, match="systemd-run --user --scope is unavailable"):
        scheduler._launch_external_cron_worker(_job())


def test_prefer_degrades_to_the_in_process_path(monkeypatch):
    """`prefer`: sem scope o job segue pelo caminho in-process (False = sem handoff)."""
    monkeypatch.setattr(process_registry, "restart_safe_gateway_child_argv", _raise_scope_unavailable)
    monkeypatch.setattr(scheduler, "load_config", lambda: {"cron": {"restart_safe_scope": "prefer"}})

    assert scheduler._launch_external_cron_worker(_job()) is False


def test_prefer_never_writes_a_handoff_payload(monkeypatch, tmp_path):
    """A degradação acontece ANTES de criar payload/ack: nada fica para trás."""
    monkeypatch.setattr(process_registry, "restart_safe_gateway_child_argv", _raise_scope_unavailable)
    monkeypatch.setattr(scheduler, "load_config", lambda: {"cron": {"restart_safe_scope": "prefer"}})

    assert scheduler._launch_external_cron_worker(_job()) is False
    assert list((tmp_path / "cron" / "external-workers").glob("*")) == []
