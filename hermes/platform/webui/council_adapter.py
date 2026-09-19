"""Bounded manager-council execution using the canonical model client."""
from __future__ import annotations

from typing import Any

from hermes.platform.models.client import ChatMessage, ExactModelClient
from hermes.platform.models.model_resolver import ModelResolver
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.webui.agent_hierarchy import AgentHierarchyStore, HierarchyError


class CouncilExecutionError(RuntimeError):
    """Council execution failed without consuming a turn."""


class CouncilAdapter:
    def __init__(self, store: AgentHierarchyStore, event_store: EventStore,
                 client: ExactModelClient | None = None,
                 resolver: ModelResolver | None = None):
        self.store = store
        self.event_store = event_store
        self.client = client or ExactModelClient()
        self.resolver = resolver or ModelResolver()

    def _node_model(self, node_id: str):
        node = next((n for n in self.store.snapshot()["nodes"] if n["id"] == node_id), None)
        if not node or not node.get("enabled", True):
            raise HierarchyError("unknown or disabled council member")
        model_id = str(node.get("model") or node.get("profile") or "").strip()
        if not model_id:
            raise CouncilExecutionError(f"council member {node_id} has no model profile")
        return self.resolver.resolve(model_id)

    def run_turn(self, council_id: str, speaker_id: str, prompt: str) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        council = next((c for c in snapshot.get("councils", []) if c["id"] == council_id), None)
        if council is None:
            raise HierarchyError("unknown council")
        other = council["to_id"] if speaker_id == council["from_id"] else council["from_id"]
        model = self._node_model(speaker_id)
        history = [ChatMessage("system", "You are a manager debating a bounded decision with another manager. Be concise and evidence-driven.")]
        history.append(ChatMessage("user", f"Topic: {council['topic']}"))
        for message in council.get("messages", []):
            history.append(ChatMessage("assistant" if message["speaker_id"] == speaker_id else "user", message["message"]))
        history.append(ChatMessage("user", str(prompt).strip()))
        result = self.client.complete(model, history)
        updated = self.store.append_council_turn(council_id, speaker_id, result.content)
        self.event_store.append(Event(name="agent_council.turn", payload={
            "council_id": council_id, "speaker_id": speaker_id, "recipient_id": other,
            "turn": updated["turns_used"], "max_turns": updated["max_turns"],
            "provider": result.provider_id, "model": result.provider_model_id,
            "total_tokens": result.total_tokens,
        }))
        return {"council": updated, "content": result.content, "provider": result.provider_id,
                "model": result.provider_model_id, "total_tokens": result.total_tokens}
