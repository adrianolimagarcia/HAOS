"""D2 — publish → append: testes do EventStoreSink + integração real.

Contratos (nunca snapshots):
(a) append_from_sync persiste no EventStore REAL (temp file): Event com
    envelope completo (event_id/trace_id/correlation_id preservados) sai
    igual no replay via events_after/cursor; dict canônico também entra.
(b) sink sem store => available False e append no-op (False, nada escrito).
(c) handler_from_sync vira subscriber do EventBus (asyncio real): publish
    com topic Event.name aciona o sink + listener síncrono; exceção do
    listener não quebra o bus (outros eventos continuam).
(d) O listener do provider_router (seam sync, Emenda 4) persiste via sink:
    ExactModelRouter esgotado => evento MODEL_ROUTE_EXHAUSTED no store com
    o snapshot canônico {model_family/variant/revision, provider_chain,
    exhausted_providers}.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from hermes.platform.protocols.bus import EventBus
from hermes.platform.observability.events import Event
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.sink import (
    EventStoreSink, _coerce_event, route_exhausted_listener,
)
from hermes.platform.models.profiles import ModelIdentity, ModelProfile, ProviderRoute
from hermes.platform.models.circuit_breaker import CircuitBreaker
from hermes.platform.models.provider_router import (
    ExactModelRouter, subscribe_route_exhausted, unsubscribe_route_exhausted,
)


class TestSinkAppend(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(str(Path(self._tmp.name) / "events.db"))
        self.sink = EventStoreSink(self.store)

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_event_preserves_envelope(self):
        event = Event(
            name="task.started", payload={"task_id": "T-1"},
            trace_id="tr-1", correlation_id="c-1",
        )
        self.assertTrue(self.sink.append_from_sync(event))
        self.assertEqual(self.sink.appended, 1)
        after = self.store.events_after(0)
        self.assertEqual(len(after), 1)
        got = after[0]
        self.assertEqual(got.name, "task.started")
        self.assertEqual(got.event_id, event.event_id)
        self.assertEqual(got.trace_id, "tr-1")
        self.assertEqual(got.correlation_id, "c-1")
        self.assertEqual(got.seq, 1)

    def test_append_dict_canonical(self):
        self.assertTrue(self.sink.append_from_sync({
            "name": "eval.performance", "payload": {"score": 0.9},
            "trace_id": "tr-x", "correlation_id": "c-x",
        }))
        after = self.store.events_after(0)
        self.assertEqual(after[0].name, "eval.performance")
        self.assertEqual(after[0].trace_id, "tr-x")
        self.assertEqual(after[0].correlation_id, "c-x")

    def test_sink_without_store_noop(self):
        sink = EventStoreSink()
        self.assertFalse(sink.available())
        self.assertFalse(sink.append_from_sync(Event(name="x", payload={})))
        self.assertEqual(sink.appended, 0)

    def test_coerce_requires_event_shaped(self):
        with self.assertRaises(TypeError):
            _coerce_event(object())


class TestSinkBusIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(str(Path(self._tmp.name) / "events.db"))
        self.sink = EventStoreSink(self.store)
        self.bus = EventBus()

    def tearDown(self):
        self._tmp.cleanup()

    def test_publish_reaches_store_and_sync_listener(self):
        received = []
        self.bus.subscribe(
            "task.created",
            self.sink.handler_from_sync(lambda e: received.append(e.name)),
        )

        async def run():
            await self.bus.publish(
                Event(name="task.created", payload={"task_id": "T-9"}, trace_id="t-9"))

        asyncio.run(run())
        self.assertEqual(received, ["task.created"])
        events = self.store.events_after(0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].trace_id, "t-9")

    def test_listener_error_does_not_break_bus(self):
        self.bus.subscribe(
            "a.event",
            self.sink.handler_from_sync(lambda e: (_ for _ in ()).throw(RuntimeError("boom"))),
        )

        async def run():
            await self.bus.publish(Event(name="a.event", payload={}))
            await self.bus.publish(Event(name="a.event", payload={"i": 2}))

        asyncio.run(run())
        # Dois eventos publicados; o erro do listener não quebrou o publish.
        self.assertEqual(self.sink.appended, 2)


class TestRouteExhaustedSink(unittest.TestCase):
    """Emenda 4 -> D2: rota esgotada publica (sink) o snapshot real."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(str(Path(self._tmp.name) / "events.db"))
        self.sink = EventStoreSink(self.store)
        self.breaker = CircuitBreaker()
        self._listener = route_exhausted_listener(self.sink)
        self.received = []
        subscribe_route_exhausted(self._listener)

    def tearDown(self):
        unsubscribe_route_exhausted(self._listener)
        self._tmp.cleanup()

    def test_exhausted_router_persists_domain_event(self):
        identity = ModelIdentity(family="test", variant="x")
        profile = ModelProfile(
            id="p", model_identity=identity,
            routes=[
                ProviderRoute(provider_id="prov-a", provider_model_id="x", priority=1),
                ProviderRoute(provider_id="prov-b", provider_model_id="x", priority=2),
            ],
        )
        # Abre o breaker das duas rotas exatas (mesma chave composta).
        for route in profile.routes:
            key = self.breaker.route_key(route.provider_id, identity)
            for _ in range(self.breaker.failure_threshold):
                self.breaker.record_failure(key)
        router = ExactModelRouter(circuit_breaker=self.breaker)
        with self.assertRaises(Exception):
            router.select_route(profile)

        events = self.store.get_all(name="provider.route_exhausted")
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["model_family"], "test")
        self.assertEqual(payload["model_variant"], "x")
        self.assertEqual(
            sorted(payload["provider_chain"]), ["prov-a", "prov-b"])
        self.assertEqual(
            sorted(payload["exhausted_providers"]), ["prov-a", "prov-b"])


if __name__ == "__main__":
    unittest.main()
