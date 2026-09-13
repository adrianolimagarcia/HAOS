from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod

@dataclass
class Capability:
    id: str
    execution_kind: str # deterministic | agentic
    providers: List[str] = field(default_factory=list)
    features: List[str] = field(default_factory=list)

class CapabilityProvider(ABC):
    """Formal capability provider contract (HAOS v1.1 Emenda 16).

    A provider may back a deterministic tool (LSP, git), an agentic worker
    (vision interpretation, security analysis) or a remote service. The
    CapabilityResolver must not need to know which kind it is: it probes,
    acquires a handle and releases it.
    """

    provider_id: str

    @abstractmethod
    async def probe(self) -> Dict[str, Any]:
        """Report availability/health/features without side effects."""

    @abstractmethod
    async def acquire(self, request: Dict[str, Any]) -> Any:
        """Obtain a capability handle for a request (may spawn a worker)."""

    @abstractmethod
    async def release(self, handle: Any) -> None:
        """Return/tear down the handle acquired by :meth:`acquire`."""

class CapabilityCycleError(RuntimeError):
    """Grafo de dependências de capabilities tem ciclo (fail-fast)."""


class CapabilityRegistry:
    def __init__(self):
        self._capabilities: Dict[str, Capability] = {}
        self._providers: Dict[str, CapabilityProvider] = {}
        self._packs: Dict[str, List[str]] = {}
        self._deps: Dict[str, List[str]] = {}
        self._register_defaults()

    def register(self, capability: Capability):
        """Registra a capability; providers de uma mesma capability fazem
        UNION (um segundo provider ACTIVE junta-se em vez de apagar o
        primeiro — port DSH/cordis dup-provide)."""
        existing = self._capabilities.get(capability.id)
        if existing is None:
            self._capabilities[capability.id] = capability
            return
        for provider in capability.providers:
            if provider not in existing.providers:
                existing.providers.append(provider)
        for feature in capability.features:
            if feature not in existing.features:
                existing.features.append(feature)

    def add_provider(self, capability_id: str, provider_id: str) -> bool:
        """Junta um provider à capability existente (aditivo; cria se faltar)."""
        capability = self._capabilities.get(capability_id)
        if capability is None:
            self._capabilities[capability_id] = Capability(
                id=capability_id, execution_kind="agentic", providers=[provider_id]
            )
            return True
        if provider_id not in capability.providers:
            capability.providers.append(provider_id)
        return True

    def remove_provider(self, capability_id: str, provider_id: str) -> bool:
        """Remove APENAS este provider; sem providers restantes, remove a
        capability (usado pelo Extension Fabric no unbridge)."""
        capability = self._capabilities.get(capability_id)
        if capability is None:
            return False
        if provider_id in capability.providers:
            capability.providers.remove(provider_id)
        if not capability.providers:
            self._capabilities.pop(capability_id, None)
        return True

    def unregister(self, capability_id: str) -> bool:
        """Remove uma capability (usado por Extension Fabric no deactivate)."""
        return self._capabilities.pop(capability_id, None) is not None

    def register_provider(self, provider: CapabilityProvider):
        """Bind a live provider instance; a provider may serve several capability ids."""
        self._providers[provider.provider_id] = provider

    def get_provider(self, provider_id: str) -> Optional[CapabilityProvider]:
        return self._providers.get(provider_id)

    def get(self, capability_id: str) -> Optional[Capability]:
        return self._capabilities.get(capability_id)

    def _register_defaults(self):
        self.register(Capability(
            id="code-intelligence",
            execution_kind="deterministic",
            providers=["hermes-lsp"],
            features=["definition", "references", "symbols", "diagnostics"]
        ))
        self.register(Capability(
            id="git",
            execution_kind="deterministic",
            providers=["native-git", "kilo"],
            features=["diff", "checkout", "worktree"]
        ))
        self.register(Capability(
            id="image-understanding",
            execution_kind="agentic",
            providers=["vision-worker"],
            features=["ocr", "layout", "interpretation"]
        ))
        self.register(Capability(
            id="multimodal-perception",
            execution_kind="deterministic",
            providers=["multimodal-dispatch-pattern"],
            features=["image", "audio", "ocr", "diagram", "auto-spawn"]
        ))
        self.register(Capability(
            id="knowledge-graph",
            execution_kind="deterministic",
            providers=["graphrag", "obsidian"],
            features=["query", "community_summary", "adr_lookup"]
        ))
        self.register(Capability(
            id="obsidian-notes",
            execution_kind="deterministic",
            providers=["obsidian"],
            features=["get_adr", "save_note", "search_vault"]
        ))
        self.register(Capability(
            id="web-search",
            execution_kind="deterministic",
            providers=["tavily", "brave", "searxng"],
            features=["search", "extract", "citations"]
        ))

    # ------------------------------------------------------------------ #
    # A4 — packs (agrupamentos) e grafo de dependências entre capabilities
    # ------------------------------------------------------------------ #
    def define_pack(self, pack_id: str, capability_ids: List[str]) -> None:
        """Pack = lista ORDENADA de capability ids (membros podem não existir
        ainda; resolução pula membros desconhecidos, como o resolver faz com
        ids avulsos). Packs de packs são achatados recursivamente na expansão."""
        self._packs[pack_id] = list(capability_ids)

    def add_dependency(self, capability_id: str, depends_on_id: str) -> None:
        """capability_id exige depends_on_id (deps entram primeiro na resolução).

        Falha rápido: capability desconhecida, autodependência ou ciclo."""
        if self.get(capability_id) is None or self.get(depends_on_id) is None:
            raise ValueError(
                f"add_dependency requires registered capabilities: "
                f"{capability_id!r} -> {depends_on_id!r}"
            )
        if capability_id == depends_on_id:
            raise ValueError(f"self-dependency not allowed: {capability_id}")
        deps = self._deps.setdefault(capability_id, [])
        if depends_on_id in deps:
            return
        if self._creates_cycle(capability_id, depends_on_id):
            raise CapabilityCycleError(
                f"dependency {capability_id} -> {depends_on_id} would create a cycle"
            )
        deps.append(depends_on_id)

    def _creates_cycle(self, capability_id: str, depends_on_id: str) -> bool:
        """depends_on_id (transitivamente) depende de capability_id?"""
        stack = [depends_on_id]
        seen = set()
        while stack:
            node = stack.pop()
            if node == capability_id:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(self._deps.get(node, []))
        return False

    def expand_requirements(self, required_ids: List[str]) -> List[str]:
        """Achata packs recursivamente (sem repetir); devolve ids de
        capabilities (ordem declarada)."""
        out: List[str] = []
        seen: set = set()

        def walk(entry: str) -> None:
            if entry in seen:
                return
            seen.add(entry)
            if entry in self._packs:
                for member in self._packs[entry]:
                    walk(member)
            else:
                out.append(entry)

        for rid in required_ids or []:
            walk(rid)
        return out

    def dependency_closure(self, required_ids: List[str]) -> List[str]:
        """Dependências transitivas primeiro (topológica), membros de packs
        incluídos; ids desconhecidos são ignorados como no resolver."""
        expanded = self.expand_requirements(required_ids)
        result: List[str] = []
        done: set = set()

        def visit(cap_id: str) -> None:
            if cap_id in done:
                return
            done.add(cap_id)
            for dep in self._deps.get(cap_id, []):
                visit(dep)
            if cap_id in self._capabilities:
                result.append(cap_id)

        for rid in expanded:
            visit(rid)
        return result

class CapabilityResolver:
    def __init__(
        self,
        registry: Optional[CapabilityRegistry] = None,
        tool_filter: Optional[Any] = None,
    ):
        self.registry = registry or CapabilityRegistry()
        self.tool_filter = tool_filter

    def resolve_requirements(self, required_ids: List[str]) -> Dict[str, Capability]:
        """Resolve ids/packs com dependências transitivas primeiro.

        A4: packs expandem para os membros; dependências entram antes de quem
        depende; ids desconhecidos continuam sendo ignorados (fail-open por
        design — capability ausente não quebra a task, só não é resolvida)."""
        resolved = {}
        for cap_id in self.registry.dependency_closure(required_ids):
            cap = self.registry.get(cap_id)
            if cap:
                resolved[cap_id] = cap
        return resolved

    def filter_tools_for_task(
        self,
        tool_schemas: List[Dict[str, Any]],
        task: Optional[Any] = None,
        posture: Optional[Any] = None,
        required_packs: Optional[Any] = None,
        circuit_breaker: Optional[Any] = None,
        packs: Optional[Any] = None,
        min_trust: Optional[Any] = None,
        max_tools: Optional[int] = 50,
    ) -> List[Dict[str, Any]]:
        """Filter tools dynamically for a task and posture using MCPToolFilter."""
        if self.tool_filter is not None:
            return self.tool_filter.filter_tools(
                task=task,
                posture=posture,
                raw_tools=tool_schemas,
                min_trust=min_trust,
                max_tools=max_tools,
                required_packs=required_packs,
            )
        from hermes.platform.capabilities.mcp.unified_fabric import MCPToolFilter

        return MCPToolFilter.filter_tools_for_task(
            tool_schemas=tool_schemas,
            task=task,
            posture=posture,
            required_packs=required_packs,
            circuit_breaker=circuit_breaker,
            packs=packs,
            min_trust=min_trust,
            max_tools=max_tools,
        )
