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

import hashlib
import logging
import re
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
            # Fork HAOS: resolve o vault pelo home canônico (get_hermes_home),
            # nunca por ~/.hermes nem por caminho relativo ao cwd — ambos
            # produziam split-brain (vault invisível) fora da ISO.
            from hermes_constants import get_hermes_home

            home = Path(get_hermes_home())
            self.vault_path = vault_path or (home / "obsidian_vault")
        self.obsidian = obsidian_adapter or ObsidianAdapter(self.vault_path)
        self.decisions = decision_store or DecisionStore()
        self.graphrag = graphrag_adapter or GraphRAGAdapter()
        self._session_id: str = ""
        from hermes_constants import get_hermes_home

        self._hermes_home: Path = Path(get_hermes_home())
        self._initialized: bool = False
        self._prefetch_cache: Dict[tuple[str, str], str] = {}
        self._turn_seen: set[tuple[str, str]] = set()

    def is_available(self) -> bool:
        """Check that the configured vault is usable without performing network I/O."""
        try:
            return self.vault_path.parent.exists()
        except OSError:
            return False

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
        """Return cached hybrid recall; populate it synchronously on a cold key."""
        if not self._initialized:
            return ""
        key = (session_id or self._session_id, query.strip())
        cached = self._prefetch_cache.get(key)
        if cached is not None:
            return cached

        blocks: List[str] = []
        seen: set[str] = set()
        for source_items, limit in (
            (self.obsidian.retrieve(query=query), 3),
            (self.decisions.retrieve(query=query), 2),
            (self.graphrag.retrieve(query=query), 2),
        ):
            for item in source_items[:limit]:
                if item.source_uri in seen:
                    continue
                seen.add(item.source_uri)
                blocks.append(f"[{item.source_uri}] {item.get_representation('summary')}")
        result = "\n\n".join(blocks)
        self._prefetch_cache[key] = result
        return result

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Prepare recall without making the next turn wait on a cold search."""
        if not self._initialized:
            return
        from agent.memory_provider import spawn_context_thread
        thread = spawn_context_thread(
            self.prefetch,
            name="hermes-fabric-prefetch",
            args=(query,),
            kwargs={"session_id": session_id or self._session_id},
        )
        thread.start()

    @staticmethod
    def _stable_memory_id(content: str, metadata: Dict[str, Any]) -> str:
        explicit = metadata.get("id")
        if explicit:
            return str(explicit)
        digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:20]
        return f"ADR-{digest}"

    def sync_turn(self, user_message: str, assistant_response: str, **kwargs: Any) -> None:
        """Persist only explicit decisions from completed turns; ordinary chat stays ephemeral."""
        if not self._initialized:
            return
        content = assistant_response.strip()
        if not content or not re.search(r"(?:^|\s)(?:ADR|DECISION)\s*[:#-]", content, re.IGNORECASE):
            return
        metadata = dict(kwargs.get("metadata") or {})
        metadata.setdefault("provenance", [f"session://{kwargs.get('session_id') or self._session_id}"])
        metadata.setdefault("source_turn", user_message[:500])
        self._upsert_memory(content, target="architecture", metadata=metadata)

    def shutdown(self) -> None:
        """Encerra recursos de memória de forma graciosa."""
        self._prefetch_cache.clear()
        self._turn_seen.clear()
        self._initialized = False

    def remember(self, content: str, target: str = "notes", metadata: Optional[Dict[str, Any]] = None) -> None:
        """Grava de modo idempotente uma decisão ou fato explícito."""
        self._upsert_memory(content, target=target, metadata=metadata)

    def _upsert_memory(self, content: str, target: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = dict(metadata or {})
        if not ("ADR" in content or target == "architecture"):
            return
        dec_id = self._stable_memory_id(content, meta)
        key = (dec_id, hashlib.sha256(content.encode("utf-8")).hexdigest())
        if key in self._turn_seen:
            return
        self._turn_seen.add(key)
        title = str(meta.get("title") or "Architecture Decision")
        supersedes = meta.get("supersedes")
        if isinstance(supersedes, str):
            supersedes = [supersedes]
        self.decisions.record_decision(dec_id, title, content, supersedes=supersedes if isinstance(supersedes, list) else None)
        obs_path = f"20-Architecture/{dec_id}.md"
        note_path = self.vault_path / obs_path
        note_body = (
            f"**Scope:** `{meta.get('scope', 'project')}` | "
            f"**Confidence:** `{meta.get('confidence', 1.0):.2f}`\n\n"
            f"{content}"
        )
        if not note_path.exists():
            self.obsidian.write_note(obs_path, title, note_body, doc_type="architecture_decision", metadata=meta)
        self.graphrag.register_entity(dec_id, "architecture_decision", content)
        for old_id in supersedes or []:
            self.graphrag.register_relation(dec_id, str(old_id), "supersedes", "Explicit decision supersession")
        self._prefetch_cache.clear()

    def on_memory_write(self, action: str, target: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Intercepta comandos de escrita da ferramenta `memory` upstream."""
        if action in {"add", "replace"}:
            self._upsert_memory(content, target=target, metadata=metadata)

