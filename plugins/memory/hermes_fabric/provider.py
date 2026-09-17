"""Plugin entrypoint para o MemoryProvider 'hermes-fabric' descoberto pelo Hermes upstream.

Permite configurar no config.yaml:
memory:
  provider: hermes-fabric
"""

from __future__ import annotations

from typing import Any

from agent.memory_provider import MemoryProvider
from hermes.platform.context.memory.provider import HermesFabricMemoryProvider


class FederatedHermesMemoryProvider(MemoryProvider):
    """Provider externo único; o Coordinator é o dono do pipeline de escrita."""

    def __init__(self) -> None:
        self._base = HermesFabricMemoryProvider()
        self._coordinator: Any = None

    @property
    def coordinator(self) -> Any:
        if self._coordinator is None:
            from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
            self._coordinator = FederatedMemoryCoordinator(
                vault_path=self._base.vault_path,
                memory_provider=self._base,
                obsidian_adapter=self._base.obsidian,
                graphrag_adapter=self._base.graphrag,
                decision_store=self._base.decisions,
            )
        return self._coordinator

    @property
    def name(self) -> str:
        return self._base.name

    def is_available(self) -> bool:
        return self._base.is_available()

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._base.initialize(session_id, **kwargs)
        if self._coordinator is not None:
            self._coordinator.close()
            self._coordinator = None

    def system_prompt_block(self) -> str:
        return self._base.system_prompt_block()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return self._base.prefetch(query, session_id=session_id)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        self._base.queue_prefetch(query, session_id=session_id)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "", messages: Any = None, turn_author: Any = None) -> None:
        content = assistant_content.strip()
        if content and ("ADR" in content or "DECISION" in content):
            self.coordinator.ingest_candidate_fact(
                fact=content, scope="project", provenance=f"session://{session_id}",
                confidence=1.0, metadata={"source_turn": user_content}, sync=True,
            )
        else:
            self._base.sync_turn(user_content, assistant_content, session_id=session_id, messages=messages, turn_author=turn_author)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return self._base.get_tool_schemas()

    def on_memory_write(self, action: str, target: str, content: str, metadata: Any = None) -> None:
        if action in {"add", "replace"}:
            self.coordinator.ingest_candidate_fact(
                fact=content, scope=str((metadata or {}).get("scope") or "project"),
                provenance=(metadata or {}).get("provenance"), confidence=float((metadata or {}).get("confidence", 1.0)),
                metadata=metadata or {}, sync=True,
            )

    def shutdown(self) -> None:
        if self._coordinator is not None:
            self._coordinator.close()
        else:
            self._base.shutdown()


def register(ctx: Any) -> None:
    """Registra o runtime federado único no PluginContext."""
    ctx.register_memory_provider(FederatedHermesMemoryProvider())
