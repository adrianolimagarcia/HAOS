"""Contrato do fork HAOS: default de home é ~/.haos, nunca ~/.hermes.

Um processo sem HERMES_HOME/HAOS_HOME (cron externo, python cru, unidade
systemd genérica) precisa cair no MESMO store do nó — ~/.haos. O default
antigo ~/.hermes produzia split-brain em instalações fora da ISO.
"""
from pathlib import Path

from hermes_constants import _get_platform_default_hermes_home


def test_default_home_do_fork_e_haos_nao_hermes(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HAOS_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path("/home/forkuser"))

    assert _get_platform_default_hermes_home() == Path("/home/forkuser/.haos")


def test_default_respeita_haos_home_env(monkeypatch):
    monkeypatch.setenv("HAOS_HOME", "/custom/node")
    monkeypatch.delenv("HERMES_HOME", raising=False)

    assert _get_platform_default_hermes_home() == Path("/custom/node")
