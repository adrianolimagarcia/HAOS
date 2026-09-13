from typing import List, Dict, Any
from hermes.platform.observability.events import Event
from hermes.platform.observability.event_store import EventStore

class TraceCollector:
    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    def get_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        events = self.event_store.get_by_trace_id(trace_id)
        return [e.to_dict() for e in events]

    def render_trace_timeline(self, trace_id: str) -> str:
        events = self.event_store.get_by_trace_id(trace_id)
        lines = [f"=== TRACE TIMELINE: {trace_id} ==="]
        for e in events:
            lines.append(f"[{e.timestamp:.3f}] {e.name} (trust={e.trust_level}) -> payload={e.payload}")
        return "\n".join(lines)
