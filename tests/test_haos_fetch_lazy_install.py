"""Contrato do fork HAOS: `haos-fetch` resolve o backend Scrapling no primeiro uso.

O extra `fetch` NÃO está em `[all]` (política 2026-05-12: backend opt-in vive em LAZY_DEPS e
resolve no primeiro uso). Sem essa resolução o CLI morria com ModuleNotFoundError cru — o que
acontecia na VM de aceitação, cujo venv nunca recebeu o pré-install do instalador. Com ela, o
venv se recupera sozinho; quando o lazy-install está desabilitado, a saída diz exatamente o
que instalar em vez de vazar um traceback.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "haos_fetch_cf.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("haos_fetch_cf", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sem_scrapling(monkeypatch):
    """Faz `import scrapling` falhar de forma determinística (None em sys.modules)."""
    monkeypatch.setitem(sys.modules, "scrapling", None)
    monkeypatch.delitem(sys.modules, "scrapling.fetchers", raising=False)


def test_import_fetchers_pede_o_backend_lazy_e_explica_a_instalacao(monkeypatch, sem_scrapling):
    module = _load_script()
    pedidos: list = []
    fake = types.ModuleType("tools.lazy_deps")

    def _ensure(feature, *, prompt=True):
        pedidos.append((feature, prompt))
        raise RuntimeError("lazy installs disabled")

    fake.ensure = _ensure
    monkeypatch.setitem(sys.modules, "tools.lazy_deps", fake)

    with pytest.raises(SystemExit) as excinfo:
        module._import_fetchers()

    assert pedidos == [("fetch.scrapling", False)]
    assert "scrapling[fetchers]==0.4.15" in str(excinfo.value)


def test_import_fetchers_funciona_apos_o_lazy_install(monkeypatch, sem_scrapling):
    module = _load_script()
    fake = types.ModuleType("tools.lazy_deps")

    def _ensure(feature, *, prompt=True):
        assert feature == "fetch.scrapling"
        fetchers = types.ModuleType("scrapling.fetchers")
        fetchers.Fetcher = "Fetcher"
        fetchers.StealthyFetcher = "StealthyFetcher"
        monkeypatch.setitem(sys.modules, "scrapling", types.ModuleType("scrapling"))
        monkeypatch.setitem(sys.modules, "scrapling.fetchers", fetchers)

    fake.ensure = _ensure
    monkeypatch.setitem(sys.modules, "tools.lazy_deps", fake)

    assert module._import_fetchers() == ("Fetcher", "StealthyFetcher")
