"""Event-sourced bot knowledge pages; EventStore is the sole ledger."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import uuid
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event

_PAGE_UPSERTED = "bot.knowledge.page_upserted"
_PAGE_DISPUTED = "bot.knowledge.page_disputed"
_PAGE_RESOLVED = "bot.knowledge.page_dispute_resolved"

@dataclass
class KnowledgePage:
    id: str
    bot_id: str
    title: str
    content: str
    citations: List[str] = field(default_factory=list)
    confidence: float = 0.5
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    revision: int = 1
    disputed: bool = False
    dispute_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.id or not self.bot_id or not self.title:
            raise ValueError("id, bot_id and title are required")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.revision < 1:
            raise ValueError("revision must be positive")

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "bot_id": self.bot_id, "title": self.title,
                "content": self.content, "citations": list(self.citations),
                "confidence": self.confidence, "valid_from": self.valid_from,
                "valid_until": self.valid_until, "revision": self.revision,
                "disputed": self.disputed, "dispute_reason": self.dispute_reason}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgePage":
        return cls(**{k: data[k] for k in ("id","bot_id","title","content","citations","confidence","valid_from","valid_until","revision","disputed","dispute_reason") if k in data})

class KnowledgePageManager:
    """Materializes pages and history exclusively by replaying EventStore."""
    def __init__(self, event_store: EventStore): self.event_store = event_store

    def _events(self, page_id: Optional[str] = None) -> List[Event]:
        return [e for e in self.event_store.get_all() if e.name in (_PAGE_UPSERTED,_PAGE_DISPUTED,_PAGE_RESOLVED) and (page_id is None or e.payload.get("page_id") == page_id)]

    def _state(self) -> Dict[str, KnowledgePage]:
        state: Dict[str, KnowledgePage] = {}
        for e in self._events():
            if e.name == _PAGE_UPSERTED: state[e.payload["page_id"]] = KnowledgePage.from_dict(e.payload["page"])
            elif e.payload["page_id"] in state:
                p = state[e.payload["page_id"]]
                if e.name == _PAGE_DISPUTED: p.disputed, p.dispute_reason = True, e.payload.get("reason")
                else: p.disputed, p.dispute_reason = False, None
        return state

    def get(self, page_id: str) -> Optional[KnowledgePage]: return self._state().get(page_id)
    def list(self, bot_id: Optional[str] = None) -> List[KnowledgePage]: return [p for p in self._state().values() if bot_id is None or p.bot_id == bot_id]
    def history(self, page_id: str) -> List[KnowledgePage]: return [KnowledgePage.from_dict(e.payload["page"]) for e in self._events(page_id) if e.name == _PAGE_UPSERTED]

    def upsert(self, page: KnowledgePage) -> KnowledgePage:
        current = self.get(page.id)
        if current: page.revision = current.revision + 1
        self.event_store.append(Event(name=_PAGE_UPSERTED, payload={"page_id": page.id, "page": page.to_dict()}))
        return page

    def dispute(self, page_id: str, reason: str) -> None:
        if not self.get(page_id): raise KeyError(page_id)
        self.event_store.append(Event(name=_PAGE_DISPUTED, payload={"page_id": page_id, "reason": reason}))

    def resolve_dispute(self, page_id: str) -> None:
        if not self.get(page_id): raise KeyError(page_id)
        self.event_store.append(Event(name=_PAGE_RESOLVED, payload={"page_id": page_id}))
