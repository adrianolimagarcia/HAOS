"""HermesFabricMemoryProvider — Provedor de memória federado para o Hermes Agent.

Do ponto de vista do Hermes upstream:
Existe apenas UM MemoryProvider externo ativo (`memory.provider: hermes-fabric`).

Do ponto de vista interno do HAOS:
Existe uma federação orquestrada de fontes:
- L0: Core Memory nativo (MEMORY.md / USER.md) congelado no system prompt
- L3: ObsidianAdapter (Canônico humano e auditável)
- L4: DecisionStore (ADRs e decisões arquiteturais com controle temporal)
- L5: GraphRAGAdapter (Projeção relacional derivada de comunidades e grafos)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from hermes.platform.context.memory.decisions import DecisionStore
from hermes.platform.context.memory.graphrag import GraphRAGAdapter
from hermes.platform.context.memory.obsidian import ObsidianAdapter
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore, VALID_SCOPES
from hermes.platform.context.memory.retrieval import HybridMemoryRetriever
from hermes.platform.context.memory.access import MemoryAccessContext

logger = logging.getLogger(__name__)


class HermesFabricMemoryProvider(MemoryProvider):
    """MemoryProvider federado que orquestra Obsidian, GraphRAG e DecisionStore."""

    def __init__(
        self,
        vault_path: Optional[Path | str] = None,
        obsidian_adapter: Optional[ObsidianAdapter] = None,
        graphrag_adapter: Optional[GraphRAGAdapter] = None,
        decision_store: Optional[DecisionStore] = None,
        canonical_store: Optional[CanonicalMemoryStore] = None,
        write_handler: Optional[Any] = None,
        recovery_handler: Optional[Any] = None,
    ):
        if isinstance(vault_path, str):
            self.vault_path = Path(vault_path)
        else:
            # Fork HAOS: resolve o vault pelo home canônico (get_hermes_home),
            # nunca por ~/.hermes nem por caminho relativo ao cwd — ambos
            # produziam split-brain (vault invisível) fora da ISO.
            from hermes_constants import get_hermes_home

            home = Path(get_hermes_home())
            self.vault_path = vault_path or (home / "obsidian_vault")
        self.obsidian = obsidian_adapter or ObsidianAdapter(self.vault_path)
        self.decisions = decision_store or DecisionStore()
        self.graphrag = graphrag_adapter or GraphRAGAdapter()
        self.canonical_store = canonical_store
        self._write_handler = write_handler
        self._recovery_handler = recovery_handler
        self._session_scopes: Dict[str, List[str]] = {}
        self._session_access: Dict[str, MemoryAccessContext] = {}
        self._session_id: str = ""
        from hermes_constants import get_hermes_home

        self._hermes_home: Path = Path(get_hermes_home())
        self._initialized: bool = False

    def is_available(self) -> bool:
        """Sempre disponível pois usa estratégias puras e adapters resilientes."""
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Inicializa conexões e diretórios de armazenamento."""
        self._session_id = session_id
        if "hermes_home" in kwargs:
            self._hermes_home = Path(kwargs["hermes_home"])
            self.vault_path = self._hermes_home / "obsidian_vault"
            self.obsidian.set_vault_path(self.vault_path)

        self.vault_path.mkdir(parents=True, exist_ok=True)
        scopes = kwargs.get("memory_scopes", ("project", "global"))
        if not isinstance(scopes, (list, tuple)) or not all(isinstance(scope, str) and scope in VALID_SCOPES for scope in scopes):
            raise ValueError("memory_scopes must contain only valid Memory Fabric scopes")
        self._session_scopes[session_id] = list(scopes)
        access = kwargs.get("memory_access_context")
        if access is not None:
            if not isinstance(access, MemoryAccessContext):
                raise TypeError("memory_access_context must be MemoryAccessContext")
            self._session_access[session_id] = access
        self._initialized = True
        if self._recovery_handler is not None:
            self._recovery_handler()
        logger.info("HermesFabricMemoryProvider initialized for session %s at %s", session_id, self.vault_path)

    def system_prompt_block(self) -> str:
        """Bloco de memória resumido injetado no system prompt upstream."""
        # Mantém curto para preservar cache e não poluir o prompt
        return (
            "## Memory Fabric (HAOS Federation)\n"
            "- Canonical Truth: transactional Memory Fabric journal\n"
            "- Human Audit Projection: Obsidian Vault (`obsidian://`)\n"
            "- Derived Projections: DecisionStore and GraphRAG\n"
            "Use memory queries or context expand tools for deep knowledge retrieval."
        )

    @property
    def name(self) -> str:
        return "hermes-fabric"

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Esquemas de ferramentas de memória exportados para o agente."""
        return []

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return only canonical, scope-authorized records for the session."""
        if not self._initialized or self.canonical_store is None:
            return ""
        scopes = self._session_scopes.get(session_id or self._session_id, ["project", "global"])
        return HybridMemoryRetriever(self.canonical_store).format_context(
            query, scopes, limit=5, budget_chars=5000,
            access=self._session_access.get(session_id or self._session_id),
        )

    def sync_turn(self, user_message: str, assistant_response: str, **kwargs: Any) -> None:
        """Observa cada turno da conversa para identificar fatos e decisões importantes."""
        # Se a resposta contiver marcadores formais de decisão (ex: "DECISION:" ou "ADR:"),
        # pode sugerir ou gravar no store canônico.
        pass

    def shutdown(self) -> None:
        """Encerra recursos de memória de forma graciosa."""
        self._initialized = False

    def remember(self, content: str, target: str = "notes", metadata: Optional[Dict[str, Any]] = None) -> None:
        """Route external writes to the coordinator; never write projections directly."""
        meta = dict(metadata or {})
        if meta.get("fabric_committed"):
            return
        scope = meta.get("scope", "project")
        access = meta.pop("_access_context", None)
        if self._write_handler is not None:
            self._write_handler(content, scope=scope, provenance=meta.get("provenance"), metadata=meta, sync=True, access_context=access)
            return
        if self.canonical_store is None:
            raise RuntimeError("Memory Fabric writer is not configured")
        self.canonical_store.append(
            content=content, scope=scope,
            kind="decision" if target == "architecture" else "fact",
            provenance=tuple({"uri": uri} for uri in meta.get("provenance", []) if isinstance(uri, str)),
            metadata=meta, idempotency_key=meta.get("id"),
        )

    def on_memory_write(self, action: str, target: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Compatibility hook; all upstream writes enter the same command path."""
        self.remember(content, target=target, metadata=metadata)
