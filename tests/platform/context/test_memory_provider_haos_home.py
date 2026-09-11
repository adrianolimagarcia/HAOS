"""Contrato: HermesFabricMemoryProvider resolve o vault pelo home canônico.

O default antigo (~/.hermes e o relativo .hermes/obsidian_vault) era um
split-brain vivo: a engine de fabric (usada nas lanes do HAOS) apontava para um
vault invisível em instalações fora da ISO.
"""

from pathlib import Path


def test_provider_default_vault_resolve_home_canonico(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HAOS_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path("/home/forkuser"))

    from hermes.platform.context.memory.provider import HermesFabricMemoryProvider

    provider = HermesFabricMemoryProvider()

    assert provider.vault_path == Path("/home/forkuser/.haos/obsidian_vault")
    assert provider._hermes_home == Path("/home/forkuser/.haos")
    assert provider.obsidian.vault_path == Path("/home/forkuser/.haos/obsidian_vault")
