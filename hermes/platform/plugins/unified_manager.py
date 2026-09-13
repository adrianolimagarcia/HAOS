"""Plugin & Capability Unified Fabric (Etapa 3 / K3).

Formaliza o ecossistema extensível do HAOS:
- PluginManifest: Manifesto declarativo tipado com dependências, capabilities expostas,
  eventos assinados e permissões.
- UnifiedPluginManager: Gerenciamento unificado de ciclo de vida com hot reload,
  validação de permissões e isolamento.
- Tratamento de LSP, GraphRAG, Obsidian, Kilo, MCP e Providers como capacidades de primeira classe.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set


@dataclass
class PluginManifest:
    """Manifesto tipado e canônico para extensões do HAOS."""

    id: str
    name: str
    version: str = "1.0.0"
    kind: str = "capability"  # capability | memory | model-provider | tool | protocol
    description: str = ""
    capabilities_provided: List[str] = field(default_factory=list)
    permissions_required: List[str] = field(default_factory=list)  # ex.: ["fs:read", "net:outbound"]
    dependencies: List[str] = field(default_factory=list)
    events_subscribed: List[str] = field(default_factory=list)
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "description": self.description,
            "capabilities_provided": list(self.capabilities_provided),
            "permissions_required": list(self.permissions_required),
            "dependencies": list(self.dependencies),
            "events_subscribed": list(self.events_subscribed),
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PluginManifest:
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            version=data.get("version", "1.0.0"),
            kind=data.get("kind", "capability"),
            description=data.get("description", ""),
            capabilities_provided=list(data.get("capabilities_provided", [])),
            permissions_required=list(data.get("permissions_required", [])),
            dependencies=list(data.get("dependencies", [])),
            events_subscribed=list(data.get("events_subscribed", [])),
            enabled=bool(data.get("enabled", True)),
            metadata=dict(data.get("metadata", {})),
        )


class UnifiedPluginManager:
    """Gerenciador unificado de plugins com hot-reload e resolução de capabilities."""

    def __init__(self):
        self._manifests: Dict[str, PluginManifest] = {}
        self._capabilities_map: Dict[str, str] = {}  # capability_name -> plugin_id
        self._event_listeners: Dict[str, List[Callable[[Any], None]]] = {}

    def register_plugin(self, manifest: PluginManifest) -> None:
        """Registra um manifesto e mapeia as capabilities providas."""
        self._manifests[manifest.id] = manifest
        if manifest.enabled:
            for cap in manifest.capabilities_provided:
                self._capabilities_map[cap] = manifest.id

    def unregister_plugin(self, plugin_id: str) -> bool:
        """Remove o plugin e desvincula suas capabilities com segurança."""
        manifest = self._manifests.pop(plugin_id, None)
        if not manifest:
            return False
        for cap in manifest.capabilities_provided:
            if self._capabilities_map.get(cap) == plugin_id:
                self._capabilities_map.pop(cap, None)
        return True

    def hot_reload_plugin(self, manifest: PluginManifest) -> bool:
        """Executa substituição a quente do manifesto e recarrega capabilities."""
        self.unregister_plugin(manifest.id)
        self.register_plugin(manifest)
        return True

    def resolve_capability_provider(self, capability: str) -> Optional[PluginManifest]:
        """Retorna o manifesto do plugin que fornece a capability requisitada."""
        plugin_id = self._capabilities_map.get(capability)
        if plugin_id:
            return self._manifests.get(plugin_id)
        return None

    def list_active_capabilities(self) -> List[str]:
        """Lista todas as capacidades atualmente operacionais no ecossistema."""
        return sorted(list(self._capabilities_map.keys()))

    def check_permissions(self, plugin_id: str, required_permission: str) -> bool:
        """Verifica se o plugin possui a permissão declarada em seu manifesto."""
        manifest = self._manifests.get(plugin_id)
        if not manifest:
            return False
        return required_permission in manifest.permissions_required
