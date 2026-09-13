import unittest
import asyncio
from hermes.platform.protocols.bus import EventBus
from hermes.platform.observability.events import Event, MessageEnvelope
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.traces import TraceCollector
from hermes.platform.observability.metrics import MetricsCollector
from hermes.platform.observability.replay import EventReplayer

class TestObservability(unittest.TestCase):
    def test_event_creation(self):
        e = Event(name="task.created", payload={"task_id": "T-1"})
        self.assertEqual(e.name, "task.created")
        self.assertEqual(e.payload["task_id"], "T-1")

    def test_event_store_and_trace(self):
        store = EventStore(":memory:")
        e1 = Event(name="task.started", payload={"task_id": "T-1"}, trace_id="trace-100")
        e2 = Event(name="task.completed", payload={"task_id": "T-1"}, trace_id="trace-100")
        store.append(e1)
        store.append(e2)

        retrieved = store.get_by_trace_id("trace-100")
        self.assertEqual(len(retrieved), 2)
        self.assertEqual(retrieved[0].name, "task.started")

        collector = TraceCollector(store)
        timeline = collector.render_trace_timeline("trace-100")
        self.assertIn("task.started", timeline)

    def test_replay(self):
        store = EventStore(":memory:")
        e1 = Event(name="step1", payload={}, trace_id="trace-200")
        store.append(e1)
        replayer = EventReplayer(store)
        replayed = []
        count = replayer.replay_trace("trace-200", lambda ev: replayed.append(ev))
        self.assertEqual(count, 1)
        self.assertEqual(replayed[0].name, "step1")

    def test_async_bus(self):
        bus = EventBus()
        received = []

        async def handler(e):
            received.append(e)

        bus.subscribe("test.event", handler)

        async def run():
            await bus.publish(Event(name="test.event", payload={"key": "val"}))

        asyncio.run(run())
        self.assertEqual(len(received), 1)

    def test_metrics_latency_series(self):
        """Contrato K7: MetricsCollector retém série temporal bounded por operação
        sem quebrar a semântica legada de summary()['latencies'] (último valor)."""
        m = MetricsCollector()
        m.record_latency("llm.call", 0.5)
        m.record_latency("llm.call", 1.5)
        m.record_latency("llm.call", 1.0)

        # Legado: último valor por operação.
        self.assertEqual(m.summary()["latencies"]["llm.call"], 1.0)

        # Série temporal completa em ordem de chegada.
        self.assertEqual(m.latency_series("llm.call"), [0.5, 1.5, 1.0])

        # Stats derivados da série.
        stats = m.latency_stats("llm.call")
        self.assertIsNotNone(stats)
        self.assertEqual(stats["min"], 0.5)
        self.assertEqual(stats["max"], 1.5)
        self.assertEqual(stats["samples"], 3.0)
        self.assertAlmostEqual(stats["avg"], 1.0)

        # Operação sem amostras -> None (não inventa série vazia).
        self.assertIsNone(m.latency_stats("never.called"))
        self.assertEqual(m.latency_series("never.called"), [])

    def test_metrics_latency_series_bounded(self):
        """A janela por operação é bounded (não cresce sem limite em runtimes longos)."""
        from hermes.platform.observability.metrics import _MAX_LATENCY_SAMPLES_PER_OP
        m = MetricsCollector()
        for i in range(_MAX_LATENCY_SAMPLES_PER_OP * 2):
            m.record_latency("op", float(i))
        series = m.latency_series("op")
        self.assertEqual(len(series), _MAX_LATENCY_SAMPLES_PER_OP)
        # Janela deslizante: descartam-se as amostras mais antigas.
        self.assertEqual(series[0], float(_MAX_LATENCY_SAMPLES_PER_OP))
        self.assertEqual(series[-1], float(_MAX_LATENCY_SAMPLES_PER_OP * 2 - 1))


if __name__ == "__main__":
    unittest.main()
