"""integração ``publish → append`` (COMPLIANCE delta 40): o EventBus
continua transporte puro; quem o alimenta persiste via EventStore.

``EventStoreSink`` liga o mundo síncrono (listeners do provider_router, o
demo server) ao EventStore sem acoplamento ao asyncio do bus:
- ``append_from_sync``: espera o evento (qualquer objeto com .name/.payload/
  .trace_id/.correlation_id; ou um dict canônico) e chama ``store.append``
  com um Event reconstruído SEM re-gerar ids — preserva o envelope.
- ``handler_from_sync``: adapta o listener síncrono p/ os handlers async do
  EventBus (que publicam no bus, o sink persiste os eventos do tipo Event).

Regra (item 22): EventBus NÃO persiste; o sink decide. Append duplicado
(id repetido) continua levantando (PK do store) — idempotência por PK, não
silêncio.
"""

import json
from typing import Any, Callable, Dict, Optional

from hermes.platform.observability.events import Event
from hermes.platform.observability.event_store import EventStore


def _coerce_event(value: Any) -> Event:
    """Reconstrói Event a partir de um dict canônico ou objeto com os campos
    do envelope. NUNCA re-gera ids: event_id ausente só em dict sem id (uso
    interno), trace_id/correlation_id preservados quando presentes."""
    if isinstance(value, Event):
        return value
    if isinstance(value, dict):
        event_id = value.get("event_id") or f"ev-{abs(hash(json.dumps(value, sort_keys=True))):x}"
        return Event(
            event_id=event_id,
            name=value.get("name", ""),
            payload=value.get("payload") or {},
            trace_id=value.get("trace_id") or value.get("event_id") or event_id,
            correlation_id=value.get("correlation_id"),
            causation_id=value.get("causation_id"),
            trust_level=value.get("trust_level", "internal"),
            schema_version=value.get("schema_version", 1),
            timestamp=value.get("timestamp") or 0.0,
        )
    name = getattr(value, "name", None)
    if name is None:
        raise TypeError(
            "EventStoreSink espera um Event, um dict canônico ou objeto com "
            f".name; recebeu {type(value).__name__}")
    return Event(
        event_id=getattr(value, "event_id", None) or f"ev-{abs(hash(str(value))):x}",
        name=name,
        payload=getattr(value, "payload", {}) or {},
        trace_id=getattr(value, "trace_id", None) or name,
        correlation_id=getattr(value, "correlation_id", None),
        causation_id=getattr(value, "causation_id", None),
        trust_level=getattr(value, "trust_level", "internal"),
        schema_version=getattr(value, "schema_version", 1),
        timestamp=getattr(value, "timestamp", None) or 0.0,
    )


class EventStoreSink:
    """Persiste eventos (publish → append) no EventStore, de forma síncrona.

    ``store`` pode ser None (unavailable): append vira no-op SEM levantar —
    observabilidade nunca quebra o fluxo do runtime. Disponível por
    ``available()``; auditável por ``appended`` (contador).
    """

    def __init__(self, store: Optional[EventStore] = None):
        self.store = store
        self.appended = 0

    def available(self) -> bool:
        return self.store is not None

    def append_from_sync(self, event: Any) -> bool:
        if self.store is None:
            return False
        coerced = _coerce_event(event)
        self.store.append(coerced)
        self.appended += 1
        return True

    def handler_from_sync(
        self, listener: Callable[[Any], None]
    ) -> Callable[[Any], Any]:
        """Envolve um listener síncrono num handler async p/ EventBus.subscribe.

        O listener recebe o Event já persistido (não o objeto do bus) e
        exceções do listener nunca quebram o publish (try/except)."""
        import asyncio

        async def _handler(event: Any) -> None:
            try:
                self.append_from_sync(event)
                listener(coerced := _coerce_event(event))
            except Exception:
                # Listener de observabilidade nunca quebra o bus.
                return None

        return _handler


def make_store_sink(store: Optional[EventStore] = None) -> EventStoreSink:
    """Fábrica — o sink default da assembléia (runtime HAOS)."""
    return EventStoreSink(store=store)


def route_exhausted_listener(
    sink: EventStoreSink,
) -> Callable[[Dict[str, Any]], bool]:
    """Listener do seam do ExactModelRouter (Emenda 4 → publish→append).

    O router emite o SNAPSHOT de domínio (payload estável, sem name); este
    listener nomeia o evento canônico (``provider.route_exhausted``) e
    persiste — a integração que a rota deixa explícita no router."""
    from hermes.platform.observability.events import ROUTE_EXHAUSTED, Event

    def _listener(snapshot: Dict[str, Any]) -> bool:
        event = Event(
            name=ROUTE_EXHAUSTED,
            payload=dict(snapshot),
            trace_id=snapshot.get("trace_id")
            or f"route-{snapshot.get('model_family', '?')}-{snapshot.get('model_variant', '?')}",
            correlation_id=snapshot.get("correlation_id"),
        )
        return sink.append_from_sync(event)

    return _listener
