from typing import List, Callable, Dict, Any, Optional, Collection, TypeVar
from hermes.platform.observability.events import Event
from hermes.platform.observability.event_store import EventStore

T = TypeVar("T")

class ReplayError(Exception):
    """Falha de replay: ordem não determinística ou evento desconhecido
    sem marcador ``ignorable`` (fail-closed, espelha DSH types.ts:456-465)."""

class EventReplayer:
    """Replay do trace por event stream (port DSH core/session).

    Fase 1: ``replay_fold`` é a variante PURA e determinística — dobra os
    eventos do trace na ordem de seq (append order) sobre um acumulador,
    sem efeito colateral; rodar duas vezes sobre o mesmo trace produz estado
    idêntico. ``replay_trace`` permanece como variante com efeito colateral
    (handler por evento) e aborta com a posição alcançada se o handler lançar.

    Política de eventos desconhecidos (fail-closed): quando ``known_names`` é
    fornecido, um evento cujo ``name`` não está na lista SÓ passa se carregar
    ``payload.ignorable is True`` (vocabulary-growth policy do DSH); caso
    contrário ``replay_fold`` levanta ``ReplayError`` reportando a posição.
    Com ``on_unknown="ignore"``, desconhecidos não-ignoráveis são pulados sem
    corromper a ordem dos demais.
    """

    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    def replay_trace(self, trace_id: str, handler: Callable[[Event], None]) -> int:
        """Re-entrega os eventos do trace ao handler em ordem de seq.

        Variante com efeito colateral: se o handler lançar, a exceção aborta
        com a posição alcançada (nenhuma re-execução parcial é silenciosa)."""
        events = self.event_store.get_by_trace_id(trace_id)
        for position, event in enumerate(events, start=1):
            handler(event)
        return len(events)

    def replay_fold(
        self,
        trace_id: str,
        projector: Callable[[T, Event], T],
        initial: T,
        *,
        known_names: Optional[Collection[str]] = None,
        on_unknown: str = "raise",  # "raise" | "ignore"
    ) -> T:
        """Dobra o trace em ordem de seq sobre o acumulador (puro).

        ``projector`` deve ser pura (sem efeito colateral) — a garantia de
        determinismo do replay depende disso (mesmo trace, mesmo estado final).
        """
        events = self.event_store.get_by_trace_id(trace_id)
        accumulator = initial
        for position, event in enumerate(events, start=1):
            if known_names is not None and event.name not in known_names:
                ignorable = bool(event.payload.get("ignorable", False))
                if ignorable:
                    continue  # vocabulary-growth: informativo, pode ser pulado
                if on_unknown != "ignore":
                    raise ReplayError(
                        f"Unknown event '{event.name}' at position {position} "
                        f"(after {position - 1} folds); payload has no "
                        f"'ignorable' marker. Pass known_names incl. it or use "
                        f"on_unknown='ignore'."
                    )
                continue  # on_unknown="ignore": pula sem corromper a ordem
            accumulator = projector(accumulator, event)
        return accumulator
