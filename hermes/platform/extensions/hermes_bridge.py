"""Bridge: Extension Fabric <-> PluginManager real do Hermes (K2 / Emenda 24).

Nível de operação: **manifest/discovery apenas**. Plugins Hermes reais
(``plugins/<cat>/<name>/plugin.yaml`` ou ``plugin.json``, além de dirs de
usuário) são descobertos via ``hermes_cli.plugins_discovery.scan_directory`` e
registrados como extensões HAOS com lifecycle/deps/capacidades derivadas do
manifesto upstream — **sem importar o código do plugin**.

Por que sem importar: carregar/executar plugins é responsabilidade do
PluginManager dentro da sessão do kernel (o AGENTS do upstream proíbe
double-instantiate, ex. model-providers). O HAOS mantém o *registro* (quem
existe, o que provê, de onde carrega) e o handoff: ``permissions`` guarda o
caminho do manifesto e as dependências python para uma fatia futura de runtime
pedir ao PluginManager a ativação real.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional

from hermes.platform.extensions.registry import (
    ACTIVE,
    DISPOSED,
    ExtensionManifest,
    ExtensionRegistry,
)

from hermes_cli.plugins_discovery import PluginManifest, scan_directory


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "plugin"


def default_plugins_root() -> Optional[Path]:
    """Raiz do repo: ``<workspace>/plugins`` (hermes/platform/extensions/...)."""
    candidate = Path(__file__).resolve().parents[3] / "plugins"
    return candidate if candidate.is_dir() else None


def discover_hermes_plugins(plugins_root: Optional[Path] = None,
                            source: str = "bundled") -> List[PluginManifest]:
    """Manifestos reais de plugins Hermes sob ``plugins_root`` (default: repo)."""
    root = Path(plugins_root) if plugins_root is not None else default_plugins_root()
    if root is None or not root.is_dir():
        return []
    return scan_directory(root, source=source)


def extension_id_for(manifest: PluginManifest) -> str:
    base = manifest.key or manifest.name
    return f"hermes:{_safe_token(manifest.source or 'plugin')}:{_safe_token(base)}"


def to_extension_manifest(manifest: PluginManifest) -> ExtensionManifest:
    """Mapeia PluginManifest upstream -> ExtensionManifest HAOS (sem importar código)."""
    provides: List[str] = []
    for cap in manifest.capabilities:
        provides.append(f"capability:{cap}")
    for tool in manifest.provides_tools:
        provides.append(f"tool:{tool}")
    # Marcador canônico por plugin (qualquer plugin real provê sua própria capability).
    name_token = _safe_token(manifest.name or manifest.key or "plugin")
    provides.append(f"capability:hermes.plugin.{name_token}")
    if not manifest.capabilities:
        provides.append(f"capability:hermes.plugin.kind.{_safe_token(manifest.kind or 'standalone')}")

    return ExtensionManifest(
        id=extension_id_for(manifest),
        version=manifest.version or "0.0.0",
        description=manifest.description or f"Hermes plugin '{manifest.name}'",
        provides=provides,
        requires=[],  # gate de capability: é resolvido no activate() pelo registry
        optional=[],
        permissions={
            "hermes_plugin_name": manifest.name,
            "hermes_manifest_path": manifest.path,
            "kind": manifest.kind or "standalone",
            "source": manifest.source or "",
            "requires_env": list(manifest.requires_env),
            "python_dependencies": list(manifest.python_dependencies),
            "provides_hooks": list(manifest.provides_hooks),
        },
    )


def bridge_hermes_plugins(
    registry: ExtensionRegistry,
    manifests: Iterable[PluginManifest],
) -> List[str]:
    """Registra manifestos reais como extensões HAOS. Retorna os ids registrados.

    Não ativa: o estado fica PENDING até o runtime pedir (activate()). O
    CapabilityRegistry do registry é usado no bridge de capabilities no
    activate() — atribua ``registry.capability_registry`` antes se quiser."""
    registered: List[str] = []
    for manifest in manifests:
        ext = to_extension_manifest(manifest)
        if registry.get(ext.id) is None:
            registry.register(ext)
            registered.append(ext.id)
    return registered


__all__ = [
    "ACTIVE", "DISPOSED",
    "PluginManifest",
    "discover_hermes_plugins",
    "bridge_hermes_plugins",
    "extension_id_for",
    "to_extension_manifest",
    "default_plugins_root",
]
