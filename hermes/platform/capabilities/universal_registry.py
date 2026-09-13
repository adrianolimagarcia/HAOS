"""Universal Capability Registry — Etapa 3: Plugin + Capability Fabric Unification.

Unifies every external and internal capability across HAOS into a uniform registered model:
- Plugins Hermes
- MCP Packs (`mcp:github`, `mcp:fetch`, etc.)
- LSP Language Servers (`lsp:python`, `lsp:typescript`)
- Kilo Lane Worktrees (`kilo:worktree`, `kilo:git`)
- Vision & Multimodal Workers (`vision:analyzer`, `audio:transcriber`)
- Browser automation (`browser:cdp`, `browser:nav`)
- GraphRAG & Knowledge Retrieval (`graphrag:query`, `obsidian:read`)
- External ANP/A2A Federated Agents (`agent:external`)

Features:
- Capability metadata: id, category, provider_type, health_check_fn, cost_per_invocation, allowed_postures, etc.
- Dynamic capability discovery and posture-based authorization/filtering.
- Health checking and fail-open / fail-closed resolution policies.
- Strictly stdlib-only imports and PEP-420 namespace compliance.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Union


class CapabilityCategory(str, enum.Enum):
    """Categorização canônica das capabilities unificadas na plataforma."""
    PLUGIN = "plugin"
    MCP = "mcp"
    LSP = "lsp"
    KILO = "kilo"
    MULTIMODAL = "multimodal"
    BROWSER = "browser"
    KNOWLEDGE = "knowledge"
    FEDERATED_AGENT = "federated_agent"
    CUSTOM = "custom"


class FailurePolicy(str, enum.Enum):
    """Política de resolução quando a checagem de saúde ou execução falha."""
    FAIL_OPEN = "fail_open"      # Permite fallback, ignora falha ou mantém disponível com aviso
    FAIL_CLOSED = "fail_closed"  # Bloqueia/rejeita imediatamente a capability


@dataclass
class HealthStatus:
    """Resultado de verificação de integridade de uma capability."""
    healthy: bool
    message: str = "ok"
    checked_at: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CapabilityMetadata:
    """Metadados e contrato operacional de uma capability unificada."""
    id: str
    category: Union[CapabilityCategory, str]
    provider_type: str
    description: str = ""
    health_check_fn: Optional[Callable[[], Union[bool, HealthStatus]]] = None
    cost_per_invocation: float = 0.0
    allowed_postures: List[str] = field(default_factory=list)  # Empty list means available in all postures
    failure_policy: FailurePolicy = FailurePolicy.FAIL_OPEN
    features: List[str] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    cooldown_seconds: float = 30.0

    def is_posture_allowed(self, posture: str) -> bool:
        """Verifica se a postura solicitada é autorizada."""
        if not self.allowed_postures or "*" in self.allowed_postures:
            return True
        return posture.strip().lower() in [p.lower() for p in self.allowed_postures]

    def check_health(self) -> HealthStatus:
        """Executa a função de health check, tratando exceções conforme a política."""
        if self.health_check_fn is None:
            return HealthStatus(healthy=True, message="no_check_defined")
        try:
            res = self.health_check_fn()
            if isinstance(res, HealthStatus):
                return res
            if isinstance(res, bool):
                return HealthStatus(healthy=res, message="ok" if res else "health_check_false")
            return HealthStatus(healthy=bool(res), message="ok" if res else "health_check_falsy")
        except Exception as exc:
            return HealthStatus(healthy=False, message=f"health_check_exception: {exc}", details={"error": str(exc)})


class UniversalCapabilityRegistry:
    """Registro universal de capacidades da plataforma Hermes/HAOS.
    
    Unifica Plugins Hermes, MCP, LSP, Kilo, Multimodal, Browser, GraphRAG/Knowledge e ANP/A2A.
    """

    def __init__(self, default_failure_policy: FailurePolicy = FailurePolicy.FAIL_OPEN):
        self._capabilities: Dict[str, CapabilityMetadata] = {}
        self._category_index: Dict[str, Set[str]] = {}
        self._packs: Dict[str, List[str]] = {}
        self._default_failure_policy = default_failure_policy
        self._health_cache: Dict[str, HealthStatus] = {}
        self._register_canonical_defaults()

    def register(self, capability: CapabilityMetadata) -> None:
        """Registra ou sobrescreve uma capability no catálogo unificado."""
        self._capabilities[capability.id] = capability
        cat_key = (
            capability.category.value
            if isinstance(capability.category, CapabilityCategory)
            else str(capability.category).lower()
        )
        if cat_key not in self._category_index:
            self._category_index[cat_key] = set()
        self._category_index[cat_key].add(capability.id)

    def unregister(self, capability_id: str) -> bool:
        """Remove uma capability registrada."""
        if capability_id not in self._capabilities:
            return False
        cap = self._capabilities.pop(capability_id)
        cat_key = (
            cap.category.value
            if isinstance(cap.category, CapabilityCategory)
            else str(cap.category).lower()
        )
        if cat_key in self._category_index:
            self._category_index[cat_key].discard(capability_id)
        self._health_cache.pop(capability_id, None)
        return True

    def get(self, capability_id: str) -> Optional[CapabilityMetadata]:
        """Obtém os metadados de uma capability pelo ID."""
        return self._capabilities.get(capability_id)

    def list_all(self) -> List[CapabilityMetadata]:
        """Retorna todas as capacidades registradas."""
        return list(self._capabilities.values())

    def list_by_category(self, category: Union[CapabilityCategory, str]) -> List[CapabilityMetadata]:
        """Retorna capacidades pertencentes a uma categoria específica."""
        cat_key = category.value if isinstance(category, CapabilityCategory) else str(category).lower()
        cap_ids = self._category_index.get(cat_key, set())
        return [self._capabilities[cid] for cid in cap_ids if cid in self._capabilities]

    def define_pack(self, pack_name: str, capability_ids: List[str]) -> None:
        """Define um pack (agrupador de capabilities)."""
        self._packs[pack_name] = list(capability_ids)

    def get_pack(self, pack_name: str) -> Optional[List[str]]:
        """Retorna os identificadores contidos em um pack."""
        return self._packs.get(pack_name)

    def check_health(self, capability_id: str, force: bool = False) -> HealthStatus:
        """Executa verificação de integridade de uma capability específica."""
        cap = self.get(capability_id)
        if not cap:
            return HealthStatus(healthy=False, message="not_found")
        if not force and capability_id in self._health_cache:
            cached = self._health_cache[capability_id]
            # Usar cache se dentro da janela de cooldown
            if time.time() - cached.checked_at < cap.cooldown_seconds:
                return cached

        status = cap.check_health()
        self._health_cache[capability_id] = status
        return status

    def check_all_health(self, force: bool = False) -> Dict[str, HealthStatus]:
        """Verifica a saúde de todas as capabilities registradas."""
        results = {}
        for cap_id in list(self._capabilities.keys()):
            results[cap_id] = self.check_health(cap_id, force=force)
        return results

    def resolve(
        self,
        required_or_preferred: Iterable[str],
        posture: Optional[str] = None,
        max_cost_limit: Optional[float] = None,
        check_health_status: bool = True,
    ) -> List[CapabilityMetadata]:
        """Resolve dinamicamente capabilities requeridas considerando postura, custo e saúde.
        
        Comportamento:
        - Expande packs se o ID for o nome de um pack cadastrado.
        - Filtra capacidades desabilitadas (`enabled=False`).
        - Filtra autorização por postura (`allowed_postures`).
        - Aplica teto de custo (`cost_per_invocation`).
        - Aplica regras de Fail-Open e Fail-Closed no health check:
          * FAIL_CLOSED: se não estiver healthy, exclui a capability da resolução.
          * FAIL_OPEN: se não estiver healthy, inclui a capability com ressalva/degradação.
        """
        expanded_ids: List[str] = []
        for item in required_or_preferred:
            if item in self._packs:
                expanded_ids.extend(self._packs[item])
            else:
                expanded_ids.append(item)

        resolved: List[CapabilityMetadata] = []
        seen: Set[str] = set()

        for cap_id in expanded_ids:
            if cap_id in seen:
                continue
            seen.add(cap_id)

            cap = self.get(cap_id)
            if not cap:
                continue

            if not cap.enabled:
                continue

            # Verificação de postura
            if posture and not cap.is_posture_allowed(posture):
                continue

            # Verificação de custo
            if max_cost_limit is not None and cap.cost_per_invocation > max_cost_limit:
                continue

            # Verificação de integridade e fail-open / fail-closed
            if check_health_status:
                health = self.check_health(cap_id)
                if not health.healthy:
                    policy = cap.failure_policy or self._default_failure_policy
                    if policy == FailurePolicy.FAIL_CLOSED:
                        # Rejeita capability se fail-closed
                        continue
                    # Se fail-open, mantém na lista (tolerância a falhas)

            resolved.append(cap)

        return resolved

    resolve_capabilities = resolve  # Canonical ADR-002 alias

    def _register_canonical_defaults(self) -> None:
        """Registra as capacidades canônicas default cobrindo todas as categorias exigidas."""
        # 1. Plugins Hermes
        self.register(CapabilityMetadata(
            id="plugin:hermes_memory",
            category=CapabilityCategory.PLUGIN,
            provider_type="hermes_plugin",
            description="Core persistent memory and reflection plugin",
            features=["memory_search", "memory_append"],
            allowed_postures=["default", "researcher", "autonomous", "supervisor"],
            failure_policy=FailurePolicy.FAIL_OPEN,
        ))

        # 2. MCP Packs
        self.register(CapabilityMetadata(
            id="mcp:github",
            category=CapabilityCategory.MCP,
            provider_type="mcp_server",
            description="MCP tool server for GitHub operations (repos, PRs, issues)",
            features=["repo_read", "pr_review", "issue_create"],
            allowed_postures=["coding", "supervisor", "autonomous"],
            cost_per_invocation=0.001,
            failure_policy=FailurePolicy.FAIL_CLOSED,
        ))
        self.register(CapabilityMetadata(
            id="mcp:fetch",
            category=CapabilityCategory.MCP,
            provider_type="mcp_server",
            description="MCP utility tool server for raw HTTP and resource fetches",
            features=["http_get", "url_fetch"],
            allowed_postures=["default", "researcher", "coding", "autonomous"],
            cost_per_invocation=0.0005,
            failure_policy=FailurePolicy.FAIL_OPEN,
        ))

        # 3. LSP Language Servers
        self.register(CapabilityMetadata(
            id="lsp:python",
            category=CapabilityCategory.LSP,
            provider_type="lsp_daemon",
            description="Python Language Server Protocol intelligence (pyright/jedi)",
            features=["definition", "references", "diagnostics", "rename"],
            allowed_postures=["coding", "autonomous"],
            cost_per_invocation=0.0,
            failure_policy=FailurePolicy.FAIL_OPEN,
        ))
        self.register(CapabilityMetadata(
            id="lsp:typescript",
            category=CapabilityCategory.LSP,
            provider_type="lsp_daemon",
            description="TypeScript/JavaScript Language Server Protocol (vtsls/tsserver)",
            features=["definition", "references", "diagnostics", "completion"],
            allowed_postures=["coding", "autonomous"],
            cost_per_invocation=0.0,
            failure_policy=FailurePolicy.FAIL_OPEN,
        ))

        # 4. Kilo Lane Worktrees
        self.register(CapabilityMetadata(
            id="kilo:worktree",
            category=CapabilityCategory.KILO,
            provider_type="kilo_worktree_engine",
            description="Ephemeral isolated git worktree lifecycle for fast branching",
            features=["worktree_create", "worktree_cleanup", "worktree_isolate"],
            allowed_postures=["coding", "autonomous"],
            cost_per_invocation=0.0,
            failure_policy=FailurePolicy.FAIL_CLOSED,
        ))
        self.register(CapabilityMetadata(
            id="kilo:git",
            category=CapabilityCategory.KILO,
            provider_type="kilo_git_engine",
            description="High performance native git automation and stash management",
            features=["git_commit", "git_rebase", "git_status"],
            allowed_postures=["coding", "autonomous", "supervisor"],
            cost_per_invocation=0.0,
            failure_policy=FailurePolicy.FAIL_CLOSED,
        ))

        # 5. Vision & Multimodal Workers
        self.register(CapabilityMetadata(
            id="vision:analyzer",
            category=CapabilityCategory.MULTIMODAL,
            provider_type="vision_worker",
            description="Vision perception worker for image diagrams, UI screenshots and OCR",
            features=["image_describe", "diagram_parse", "ocr"],
            allowed_postures=["default", "researcher", "coding", "autonomous"],
            cost_per_invocation=0.005,
            failure_policy=FailurePolicy.FAIL_OPEN,
        ))
        self.register(CapabilityMetadata(
            id="audio:transcriber",
            category=CapabilityCategory.MULTIMODAL,
            provider_type="audio_worker",
            description="Audio speech-to-text transcriber worker",
            features=["audio_transcribe", "speaker_diarization"],
            allowed_postures=["default", "researcher", "autonomous"],
            cost_per_invocation=0.004,
            failure_policy=FailurePolicy.FAIL_OPEN,
        ))

        # 6. Browser Automation
        self.register(CapabilityMetadata(
            id="browser:cdp",
            category=CapabilityCategory.BROWSER,
            provider_type="cdp_driver",
            description="Chrome DevTools Protocol raw remote automation interface",
            features=["dom_inspect", "network_monitor", "console_logs"],
            allowed_postures=["researcher", "autonomous"],
            cost_per_invocation=0.002,
            failure_policy=FailurePolicy.FAIL_CLOSED,
        ))
        self.register(CapabilityMetadata(
            id="browser:nav",
            category=CapabilityCategory.BROWSER,
            provider_type="browser_controller",
            description="High level browser navigation, clicking, typing and snapshotting",
            features=["navigate", "click", "fill", "screenshot"],
            allowed_postures=["researcher", "autonomous"],
            cost_per_invocation=0.001,
            failure_policy=FailurePolicy.FAIL_CLOSED,
        ))

        # 7. GraphRAG & Knowledge Retrieval
        self.register(CapabilityMetadata(
            id="graphrag:query",
            category=CapabilityCategory.KNOWLEDGE,
            provider_type="graphrag_engine",
            description="Knowledge Graph augmented RAG entity & relationship query engine",
            features=["entity_search", "subgraph_traversal", "community_summary"],
            allowed_postures=["researcher", "coding", "autonomous", "default"],
            cost_per_invocation=0.003,
            failure_policy=FailurePolicy.FAIL_OPEN,
        ))
        self.register(CapabilityMetadata(
            id="obsidian:read",
            category=CapabilityCategory.KNOWLEDGE,
            provider_type="obsidian_vault_reader",
            description="Local Markdown knowledge vault and backlink explorer",
            features=["vault_search", "backlinks", "note_read"],
            allowed_postures=["researcher", "coding", "autonomous", "default"],
            cost_per_invocation=0.0,
            failure_policy=FailurePolicy.FAIL_OPEN,
        ))

        # 8. External ANP/A2A Federated Agents
        self.register(CapabilityMetadata(
            id="agent:external",
            category=CapabilityCategory.FEDERATED_AGENT,
            provider_type="anp_federated_bridge",
            description="Federated cross-agent ANP (Agent Network Protocol) bridge",
            features=["agent_handshake", "remote_invoke", "stream_events"],
            allowed_postures=["autonomous", "supervisor"],
            cost_per_invocation=0.010,
            failure_policy=FailurePolicy.FAIL_CLOSED,
        ))

        # Default Packs
        self.define_pack("pack:coding_full", [
            "lsp:python",
            "lsp:typescript",
            "kilo:worktree",
            "kilo:git",
            "mcp:github",
        ])
        self.define_pack("pack:researcher_full", [
            "browser:nav",
            "vision:analyzer",
            "graphrag:query",
            "obsidian:read",
            "mcp:fetch",
        ])
        self.define_pack("pack:multimodal_suite", [
            "vision:analyzer",
            "audio:transcriber",
        ])
