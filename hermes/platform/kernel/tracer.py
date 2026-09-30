"""Distributed Tracing and Observability for Agent Civilization.

Provides OpenTelemetry-aligned span trees tracking:
trace_id, span_id, parent_span_id, agent_id, task_id, model, tokens, latency,
tool calls, decisions, and state transitions.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    name: str
    agent_id: str
    task_id: str
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_ms: float = 0.0
    status: str = "OK"  # "OK", "ERROR", "BLOCKED", "ROLLED_BACK"
    model: str = "default"
    tokens: int = 0
    cost_usd: float = 0.0
    tools_called: List[str] = field(default_factory=list)
    decision: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def finish(
        self,
        status: str = "OK",
        tokens: int = 0,
        cost_usd: float = 0.0,
        decision: Optional[str] = None,
        error: Optional[str] = None,
        tools_called: Optional[List[str]] = None,
    ) -> None:
        self.end_time = time.time()
        self.duration_ms = max(0.0, (self.end_time - self.start_time) * 1000.0)
        self.status = status
        self.tokens += tokens
        self.cost_usd += cost_usd
        if tools_called:
            self.tools_called.extend(tools_called)
        if decision:
            self.decision = decision
        if error:
            self.events.append({"time": time.time(), "type": "error", "message": error})

    def add_event(self, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.events.append({
            "time": time.time(),
            "name": name,
            "payload": payload or {},
        })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "model": self.model,
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 6),
            "tools_called": list(self.tools_called),
            "decision": self.decision,
            "attributes": dict(self.attributes),
            "events": list(self.events),
        }


class DistributedTracer:
    """Central collector and registry for distributed agent spans."""

    def __init__(self) -> None:
        self._traces: Dict[str, List[Span]] = {}
        self._spans_by_id: Dict[str, Span] = {}

    def start_trace(self, task_id: str, root_agent_id: str, name: str = "root") -> str:
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        span_id = f"span-{uuid.uuid4().hex[:8]}"
        root_span = Span(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=None,
            name=name,
            agent_id=root_agent_id,
            task_id=task_id,
        )
        self._traces[trace_id] = [root_span]
        self._spans_by_id[span_id] = root_span
        return trace_id

    def start_span(
        self,
        trace_id: str,
        name: str,
        agent_id: str,
        task_id: str = "default",
        parent_span_id: Optional[str] = None,
        model: str = "default",
        attributes: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Span:
        attrs = dict(attributes or {})
        attrs.update(kwargs)
        span_id = f"span-{uuid.uuid4().hex[:8]}"
        span = Span(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=name,
            agent_id=agent_id,
            task_id=task_id,
            model=model,
            attributes=attrs,
        )
        if trace_id not in self._traces:
            self._traces[trace_id] = []
        self._traces[trace_id].append(span)
        self._spans_by_id[span_id] = span
        return span

    def end_span(
        self,
        span_id: Any,
        status: str = "OK",
        tokens: int = 0,
        cost_usd: float = 0.0,
        decision: Optional[str] = None,
        error: Optional[str] = None,
        tools_called: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Optional[Span]:
        target_id = span_id.span_id if hasattr(span_id, "span_id") else str(span_id)
        span = self._spans_by_id.get(target_id)
        if span:
            span.finish(
                status=status,
                tokens=tokens,
                cost_usd=cost_usd,
                decision=decision,
                error=error,
                tools_called=tools_called,
            )
        return span

    def get_trace(self, trace_id: str) -> List[Span]:
        return list(self._traces.get(trace_id, []))

    def render_trace_tree(self, trace_id: str) -> str:
        """Render hierarchical ASCII tree representation of spans."""
        spans = self.get_trace(trace_id)
        if not spans:
            return f"Trace {trace_id} not found."

        # Group by parent
        children_map: Dict[Optional[str], List[Span]] = {}
        for s in spans:
            children_map.setdefault(s.parent_span_id, []).append(s)

        lines: List[str] = [f"=== TRACE {trace_id} ==="]

        def _render_node(parent_id: Optional[str], depth: int = 0) -> None:
            children = children_map.get(parent_id, [])
            for c in children:
                indent = "  " * depth + ("└── " if depth > 0 else "")
                tool_info = f" | tools: [{', '.join(c.tools_called)}]" if c.tools_called else ""
                dec_info = f" | dec: {c.decision}" if c.decision else ""
                lines.append(
                    f"{indent}[{c.agent_id}] {c.name} "
                    f"({c.status}, {c.duration_ms:.1f}ms, {c.tokens}tok){tool_info}{dec_info}"
                )
                _render_node(c.span_id, depth + 1)

        _render_node(None, 0)
        return "\n".join(lines)


# Global tracer instance
GLOBAL_TRACER = DistributedTracer()
DistributedKernelTracer = DistributedTracer
