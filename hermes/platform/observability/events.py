import asyncio
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Callable, Awaitable, Set

@dataclass
class Event:
    name: str
    payload: Dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    trust_level: str = "internal" # core_policy, canonical_obsidian, internal, untrusted_external
    schema_version: int = 1
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

ROUTE_EXHAUSTED = "provider.route_exhausted"


def route_exhausted_event(
    *,
    model_family: str,
    model_variant: str,
    provider_chain: List[str],
    exhausted_providers: List[str],
    model_revision: str = "latest",
    correlation_id: Optional[str] = None,
    causation_id: Optional[str] = None,
) -> Event:
    """Evento de domínio p/ o Orchestrator quando TODAS as rotas de um modelo
    exato estão esgotadas/abertas no CircuitBreaker (Emenda 4)."""
    return Event(
        name=ROUTE_EXHAUSTED,
        payload={
            "model_family": model_family,
            "model_variant": model_variant,
            "model_revision": model_revision,
            "provider_chain": list(provider_chain),
            "exhausted_providers": list(exhausted_providers),
        },
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


@dataclass
class MessageEnvelope:
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    task_id: Optional[str] = None
    team_id: Optional[str] = None
    sender_agent_id: str = ""
    sender_posture: str = ""
    recipient_agent_id: str = ""
    recipient_posture: str = ""
    msg_type: str = "INFO"
    trust_level: str = "internal"
    schema_version: int = 1
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
