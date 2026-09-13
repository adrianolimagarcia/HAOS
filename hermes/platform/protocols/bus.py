import asyncio
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Type, TypeVar, Union

T = TypeVar("T")
AsyncHandler = Callable[[Any], Coroutine[Any, Any, None]]

class EventBus:
    def __init__(self, router: Optional[Any] = None) -> None:
        self._type_handlers: Dict[Type[Any], List[AsyncHandler]] = {}
        self._topic_handlers: Dict[str, List[AsyncHandler]] = {}
        self._all_handlers: List[AsyncHandler] = []
        self._router = router

    def attach_router(self, router: Any) -> None:
        """Attach a unified ProtocolRouter or bridge to this EventBus."""
        self._router = router

    @property
    def router(self) -> Optional[Any]:
        return self._router

    def subscribe(
        self,
        event_type_or_topic: Union[Type[T], str, None] = None,
        handler: AsyncHandler = None,
    ) -> Callable[[], None]:
        def decorator(fn: AsyncHandler) -> AsyncHandler:
            if event_type_or_topic is None:
                if fn not in self._all_handlers:
                    self._all_handlers.append(fn)
            elif isinstance(event_type_or_topic, str):
                handlers = self._topic_handlers.setdefault(event_type_or_topic, [])
                if fn not in handlers:
                    handlers.append(fn)
            elif isinstance(event_type_or_topic, type):
                handlers = self._type_handlers.setdefault(event_type_or_topic, [])
                if fn not in handlers:
                    handlers.append(fn)
            return fn

        if handler is not None:
            decorator(handler)

    async def publish(self, event: Any, topic: str = None) -> None:
        matched_handlers: Set[AsyncHandler] = set()

        event_cls = type(event)
        for registered_type, handlers in self._type_handlers.items():
            if isinstance(event, registered_type):
                matched_handlers.update(handlers)

        if topic and topic in self._topic_handlers:
            matched_handlers.update(self._topic_handlers[topic])

        if hasattr(event, "name") and isinstance(getattr(event, "name"), str):
            evt_name = getattr(event, "name")
            if evt_name in self._topic_handlers:
                matched_handlers.update(self._topic_handlers[evt_name])

        matched_handlers.update(self._all_handlers)

        if matched_handlers:
            tasks = [asyncio.create_task(handler(event)) for handler in matched_handlers]
            await asyncio.gather(*tasks, return_exceptions=True)

        # If an attached unified protocol router is present and event has routing metadata or is dispatchable
        if self._router is not None and hasattr(self._router, "dispatch"):
            from hermes.platform.protocols.unified_bus import ProtocolEnvelope, ProtocolType, TrustBoundary
            if isinstance(event, ProtocolEnvelope):
                await self._router.dispatch(event)
            elif hasattr(event, "to_protocol_envelope"):
                env = event.to_protocol_envelope()
                await self._router.dispatch(env)
            elif isinstance(event, dict) and "recipient" in event:
                envelope = ProtocolEnvelope(
                    protocol_type=ProtocolType(event.get("protocol_type", "INTERNAL")),
                    sender=event.get("sender", "event_bus"),
                    recipient=event["recipient"],
                    payload=event.get("payload", event),
                    trust_boundary=TrustBoundary(event.get("trust_boundary", "kernel")),
                    metadata=event.get("metadata", {}),
                )
                await self._router.dispatch(envelope)
