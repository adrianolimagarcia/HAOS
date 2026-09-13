"""MCP Fabric — K3 (Emenda 25): discovery/scope/health sobre o cliente MCP REAL do Hermes.

Nada é duplicado: o kernel já tem cliente MCP (``tools/mcp_tool*.py``); este módulo
envolve as superfícies públicas com um contrato HAOS (registry/router/health/scope
por postura). Imports do kernel são LAZY (dentro dos métodos): o módulo fica
stdlib-only em nível de módulo e não paga SDK quando não há servers configurados.

Camadas:
* ``discover_servers()`` — config.yaml ``mcp_servers:`` via ``_load_mcp_config``
  (``{}`` quando não há config — degradação limpa, sem spawn).
* ``scope_servers(...)`` — FUNÇÃO PURA: decide quais servers configurados ficam
  no escopo de uma postura/tarefa (capability_map + packs, filtro
  ``allowed_mcp_names``). Regra: server entra no escopo se alguma capability
  declarada da postura/tarefa o mapeia (ou um pack o inclui); sem declaração
  nenhuma, escopo vazio (postura não pediu MCP → nada é exposto).
* ``health()`` — ``get_mcp_status()`` do kernel (status real por server).
* ``discover_tools``/``probe_once`` — discovery de tools (``[]``/``{}`` sem o SDK
  ``mcp`` instalado — degradação stdlib é o contrato CIável).
* ``MCPCapabilityProvider(CapabilityProvider)`` — capability ``mcp``
  (execution_kind=agentic) no registry; acquire devolve handle de escopo (o
  fabric não é dono dos servers do kernel, então release é no-op).
"""

from typing import Any, Dict, Iterable, List, Optional

from hermes.platform.capabilities.registry import Capability, CapabilityProvider


class MCPFabric:
    """Registry/router/health/scope dos servers MCP do Hermes (sem duplicar cliente)."""

    provider_id = "hermes-mcp-fabric"

    def __init__(
        self,
        *,
        capability_map: Optional[Dict[str, List[str]]] = None,
        packs: Optional[Dict[str, List[str]]] = None,
    ):
        # capability-id -> server names (ex.: {"data-access": ["db-mcp"]})
        self._capability_map: Dict[str, List[str]] = dict(capability_map or {})
        # pack name -> server names (TaskSpec.mcp_packs referencia packs)
        self._packs: Dict[str, List[str]] = dict(packs or {})

    # ------------------------------------------------------------------ #
    # discovery (kernel real, lazy)
    # ------------------------------------------------------------------ #
    def discover_servers(self) -> Dict[str, dict]:
        """Servers MCP configurados em config.yaml (``mcp_servers:``). ``{}`` sem config."""
        from tools.mcp_tool_config import _load_mcp_config  # noqa: PLC0415 - lazy

        return _load_mcp_config()

    def health(self) -> List[dict]:
        """Status real por server (connected/disabled/connecting/failed/configured)."""
        from tools.mcp_tool_discovery import get_mcp_status  # noqa: PLC0415 - lazy

        return get_mcp_status()

    def discover_tools(self, allowed_mcp_names: Optional[Iterable[str]] = None) -> List[str]:
        """Discovery de tools MCP; [] sem SDK ``mcp`` (extra opcional upstream)."""
        from tools.mcp_tool_discovery import discover_mcp_tools  # noqa: PLC0415 - lazy

        names = list(allowed_mcp_names) if allowed_mcp_names is not None else None
        return discover_mcp_tools(allowed_mcp_names=names)

    def probe_once(self) -> Dict[str, Any]:
        """Probe (connect→list→disconnect) dos servers habilitados; {} sem SDK."""
        from tools.mcp_tool_discovery import probe_mcp_server_tools  # noqa: PLC0415 - lazy

        return probe_mcp_server_tools()

    def has_registered_tools(self) -> bool:
        from tools.mcp_tool_discovery import has_registered_mcp_tools  # noqa: PLC0415 - lazy

        return has_registered_mcp_tools()

    def registered_server_names(self) -> set:
        from tools.mcp_tool_discovery import get_registered_mcp_server_names  # noqa: PLC0415 - lazy

        return get_registered_mcp_server_names()

    def shutdown(self, *, scope: Optional[str] = None) -> None:
        """Shutdown dos servers que o kernel iniciou (o fabric nunca é dono, mas pode pedir)."""
        from tools.mcp_tool_lifecycle import shutdown_mcp_servers  # noqa: PLC0415 - lazy

        shutdown_mcp_servers(scope=scope)

    def reconnect(self, server_name: str) -> bool:
        from tools.mcp_tool_loop import reconnect_mcp_server  # noqa: PLC0415 - lazy

        return reconnect_mcp_server(server_name)

    # ------------------------------------------------------------------ #
    # scope (puro — decisão por postura/tarefa)
    # ------------------------------------------------------------------ #
    def set_capability_servers(self, capability_id: str, server_names: List[str]) -> None:
        """Registra quais servers satisfazem uma capability (aditivo)."""
        self._capability_map[capability_id] = list(server_names)

    def register_pack(self, pack_name: str, server_names: List[str]) -> None:
        """Pack = conjunto nomeado de servers (TaskSpec.mcp_packs)."""
        self._packs[pack_name] = list(server_names)

    def scope_servers(
        self,
        servers: Dict[str, dict],
        *,
        required_capabilities: Iterable[str] = (),
        capabilities_prefer: Iterable[str] = (),
        mcp_packs: Iterable[str] = (),
        allowed_mcp_names: Optional[Iterable[str]] = None,
    ) -> List[str]:
        """Servers configurados no escopo de postura+tarefa, ordenados.

        - ``capabilities_prefer ∪ required_capabilities`` (PostureSpec +
          TaskSpec) resolvem via capability_map;
        - ``mcp_packs`` (TaskSpec) resolvem via packs;
        - ``allowed_mcp_names`` (quando dado) é filtro restritivo final.
        """
        wanted: set = set()
        for cap in list(required_capabilities) + list(capabilities_prefer):
            wanted.update(self._capability_map.get(cap, ()))
        for pack in mcp_packs:
            wanted.update(self._packs.get(pack, ()))

        known = set(servers)
        in_scope = wanted & known
        if allowed_mcp_names is not None:
            in_scope &= set(allowed_mcp_names)
        return sorted(in_scope)


class MCPCapabilityProvider(CapabilityProvider):
    """Provider da capability ``mcp`` — probe real; acquire devolve handle de escopo.

    O fabric não possui os servers (o kernel os inicia); acquire retorna o escopo
    decidido e release é no-op — matching com o invariante "nunca duplicar cliente".
    """

    provider_id = MCPFabric.provider_id

    def __init__(self, fabric: Optional[MCPFabric] = None):
        self.fabric = fabric or MCPFabric()

    async def probe(self) -> Dict[str, Any]:
        servers = self.fabric.health()
        return {
            "available": True,
            "discovered": len(servers),
            "servers": servers,
            "status": "degraded" if not servers else "configured",
        }

    async def acquire(self, request: Dict[str, Any]) -> Dict[str, Any]:
        servers = self.fabric.discover_servers()
        names = self.fabric.scope_servers(
            servers,
            required_capabilities=request.get("required_capabilities", ()),
            capabilities_prefer=request.get("capabilities_prefer", ()),
            mcp_packs=request.get("mcp_packs", ()),
            allowed_mcp_names=request.get("allowed_mcp_names"),
        )
        return {
            "provider_id": self.provider_id,
            "server_names": names,
            "all_configured": sorted(servers),
        }

    async def release(self, handle: Any) -> None:
        return None  # handle de escopo: nada a derrubar (kernel é dono dos servers)


def register_mcp_capability(
    registry,
    *,
    fabric: Optional[MCPFabric] = None,
) -> MCPCapabilityProvider:
    """Registra capability ``mcp`` (agentic) + provider no CapabilityRegistry."""
    provider = MCPCapabilityProvider(fabric=fabric)
    registry.register(Capability(
        id="mcp",
        execution_kind="agentic",
        providers=[provider.provider_id],
        features=["server-discovery", "scope", "health"],
    ))
    registry.register_provider(provider)
    return provider
