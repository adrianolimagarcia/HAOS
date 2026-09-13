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

logger = logging.getLogger(__name__)


class HermesFabricMemoryProvider(MemoryProvider):
    """MemoryProvider federado que orquestra Obsidian, GraphRAG e DecisionStore."""

    def __init__(
        self,
        vault_path: Optional[Path | str] = None,
        obsidian_adapter: Optional[ObsidianAdapter] = None,
        graphrag_adapter: Optional[GraphRAGAdapter] = None,
        decision_store: Optional[DecisionStore] = None,
    ):
        if isinstance(vault_path, str):
            self.vault_path = Path(vault_path)
        else:
            self.vault_path = vault_path or Path(".hermes/obsidian_vault")
        self.obsidian = obsidian_adapter or ObsidianAdapter(self.vault_path)
        self.decisions = decision_store or DecisionStore()
        self.graphrag = graphrag_adapter or GraphRAGAdapter()
        self._session_id: str = ""
        self._hermes_home: Path = Path.home() / ".hermes"
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
        self._initialized = True
        logger.info("HermesFabricMemoryProvider initialized for session %s at %s", session_id, self.vault_path)

    def system_prompt_block(self) -> str:
        """Bloco de memória resumido injetado no system prompt upstream."""
        # Mantém curto para preservar cache e não poluir o prompt
        return (
            "## Memory Fabric (HAOS Federation)\n"
            "- Architecture & Project Truth: Obsidian Vault (`obsidian://`)\n"
            "- Decisions & Governance: DecisionStore (ADRs)\n"
            "- Relational & Impact Analysis: GraphRAG\n"
            "Use memory queries or context expand tools for deep knowledge retrieval."
        )

    @property
    def name(self) -> str:
        return "hermes-fabric"

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Esquemas de ferramentas de memória exportados para o agente."""
        return []

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Prefetch upstream: busca notas relevantes no Obsidian e decisões ativas."""
        if not self._initialized:
            return ""

        blocks: List[str] = []
        # Busca no Obsidian
        obs_items = self.obsidian.retrieve(query=query)
        for item in obs_items[:3]:
            blocks.append(f"[{item.source_uri}] {item.get_representation('summary')}")

        # Busca no DecisionStore
        dec_items = self.decisions.retrieve(query=query)
        for item in dec_items[:2]:
            blocks.append(f"[{item.source_uri}] {item.get_representation('summary')}")

        return "\n\n".join(blocks)

    def sync_turn(self, user_message: str, assistant_response: str, **kwargs: Any) -> None:
        """Observa cada turno da conversa para identificar fatos e decisões importantes."""
        # Se a resposta contiver marcadores formais de decisão (ex: "DECISION:" ou "ADR:"),
        # pode sugerir ou gravar no store canônico.
        pass

    def shutdown(self) -> None:
        """Encerra recursos de memória de forma graciosa."""
        self._initialized = False

    def remember(self, content: str, target: str = "notes", metadata: Optional[Dict[str, Any]] = None) -> None:
        """Grava ou notifica o provedor de memória upstream sobre um fato ou decisão."""
        meta = metadata or {}
        # Se for decisão de arquitetura, reflete no DecisionStore e Obsidian se não estiverem gravadas
        if "ADR" in content or target == "architecture":
            dec_id = meta.get("id", f"ADR-LOCAL-{int(len(content))}")
            title = meta.get("title", "Architecture Decision")
            if not self.decisions.get_decision(dec_id):
                self.decisions.record_decision(dec_id, title, content)
            obs_path = f"20-Architecture/{dec_id}.md"
            if not (self.vault_path / obs_path).exists() and not (Path(self.obsidian.vault_path) / obs_path).exists():
                self.obsidian.write_note(obs_path, title, content, doc_type="architecture_decision", metadata=meta)

    def on_memory_write(self, action: str, target: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Intercepta comandos de escrita da ferramenta `memory` upstream."""
        meta = metadata or {}
        # Se for decisão de arquitetura, reflete no DecisionStore e Obsidian
        if "ADR" in content or target == "architecture":
            dec_id = meta.get("id", f"ADR-LOCAL-{int(len(content))}")
            title = meta.get("title", "Architecture Decision")
            self.decisions.record_decision(dec_id, title, content)
            self.obsidian.write_note(f"20-Architecture/{dec_id}.md", title, content, doc_type="architecture_decision")
