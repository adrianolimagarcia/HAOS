"""Isolamento de home do ``HAOSDoctor``.

O check existe para pegar vazamento para ``~/.hermes`` (item 5 da docstring do
módulo). Ele precisa olhar o home **efetivo** do processo: ler apenas a variável
de ambiente aprova justamente o caso mais comum — o serviço cuja unit não define
``HERMES_HOME``, e cujo processo então resolve para ``~/.hermes`` pelo default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hermes_constants
from hermes.platform.diagnostics.doctor import HAOSDoctor


@pytest.fixture
def appliance(tmp_path, monkeypatch):
    """Appliance root sem usuário ``haos``: o default do fork resolve para ``~/.hermes``."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(hermes_constants, "_node_store_for_root_operator", lambda: None)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HAOS_HOME", raising=False)
    (tmp_path / ".haos").mkdir()
    return tmp_path


def test_unset_hermes_home_that_resolves_to_dot_hermes_is_flagged(appliance):
    """Sem ``HERMES_HOME`` o processo cai em ``~/.hermes`` — o check tem que acusar."""
    result = HAOSDoctor(haos_home=appliance / ".haos").check_env_isolation()

    assert result.status == "WARN", result.message
    assert result.details["source"] == "default"


def test_whitespace_hermes_home_is_treated_as_unset(appliance, monkeypatch):
    """``HERMES_HOME=   `` é ausência disfarçada: o home efetivo continua sendo ``~/.hermes``."""
    monkeypatch.setenv("HERMES_HOME", "   ")

    result = HAOSDoctor(haos_home=appliance / ".haos").check_env_isolation()

    assert result.status == "WARN", result.message
    assert result.details["source"] == "default"


def test_explicit_hermes_home_elsewhere_is_flagged_with_its_source(appliance, monkeypatch):
    """Quando a variável *é* a causa, o relatório tem que dizer isso — não só o status."""
    monkeypatch.setenv("HERMES_HOME", str(appliance / ".hermes"))

    result = HAOSDoctor(haos_home=appliance / ".haos").check_env_isolation()

    assert result.status == "WARN", result.message
    assert result.details["source"] == "env"


def test_real_haos_home_is_not_flagged(appliance, monkeypatch):
    """Contrato inverso: apontar para o home do HAOS nunca pode virar WARN."""
    monkeypatch.setenv("HERMES_HOME", str(appliance / ".haos"))

    result = HAOSDoctor(haos_home=appliance / ".haos").check_env_isolation()

    assert result.status == "PASS", result.message
