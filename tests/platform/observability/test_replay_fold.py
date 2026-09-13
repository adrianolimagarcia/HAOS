"""EventStore ordenação por seq + replay puro (port DSH core/session, Fase 1).

Contracts (relações de comportamento, não snapshots):
1. seq é estritamente crescente por append e a ordem de leitura é a de append
   mesmo com timestamps idênticos (determinística por seq, não por relógio).
2. events_after(cursor) retoma a partir do seq (replay incremental);
   cursor() é o maior seq persistido.
3. append com event_id duplicado levanta (idempotência por PK, não silêncio).
4. replay_fold é puro/determinístico: dobrar o mesmo trace duas vezes produz
   o mesmo acumulador; ordem do fold == ordem de append.
5. Política fail-closed: evento desconhecido sem marcador 'ignorable' levanta
   ReplayError com a posição; ignorable é pulado; on_unknown='ignore' pula.
"""

import unittest

from hermes.platform.observability.events import Event
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.replay import EventReplayer, ReplayError


_COUNTER = iter(range(1000))


def _event(name, trace_id, payload=None, event_id=None, ts=0.0):
    return Event(name=name, payload=payload or {}, trace_id=trace_id,
                 event_id=event_id or f"{trace_id}-{next(_COUNTER)}-{name}",
                 timestamp=ts)


class TestEventStoreOrdering(unittest.TestCase):
    def test_seq_strictly_increasing_and_order_by_append(self):
        store = EventStore(":memory:")
        # Timestamps idênticos de propósito: a ordem TEM que vir de seq.
        store.append(_event("a", "t1", ts=1.0))
        store.append(_event("b", "t1", ts=1.0))
        store.append(_event("c", "t1", ts=1.0))
        events = store.get_by_trace_id("t1")
        self.assertEqual([e.name for e in events], ["a", "b", "c"])
        seqs = [e.seq for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), 3)

    def test_cursor_and_events_after_resume(self):
        store = EventStore(":memory:")
        self.assertEqual(store.cursor(), 0)
        store.append(_event("a", "t1", event_id="e1"))
        store.append(_event("b", "t2", event_id="e2"))
        self.assertEqual(store.cursor(), 2)
        after = store.events_after(1)
        self.assertEqual([e.event_id for e in after], ["e2"])
        # Filtro por trace + retomada.
        store.append(_event("c", "t1", event_id="e3"))
        self.assertEqual([e.event_id for e in store.events_after(1, trace_id="t1")], ["e3"])

    def test_duplicate_event_id_raises(self):
        store = EventStore(":memory:")
        store.append(_event("a", "t1", event_id="dup"))
        with self.assertRaises(Exception):
            store.append(_event("b", "t1", event_id="dup"))


class TestReplayFold(unittest.TestCase):
    def test_fold_order_matches_append_and_is_deterministic(self):
        store = EventStore(":memory:")
        store.append(_event("add", "tr", payload={"n": 1}))
        store.append(_event("add", "tr", payload={"n": 2}))
        store.append(_event("add", "tr", payload={"n": 3}))
        replayer = EventReplayer(store)

        def project(total, event):
            return total + event.payload["n"]

        first = replayer.replay_fold("tr", project, 0)
        second = replayer.replay_fold("tr", project, 0)
        self.assertEqual(first, 6)
        self.assertEqual(second, first)  # puro/determinístico

    def test_unknown_non_ignorable_raises_at_position(self):
        store = EventStore(":memory:")
        store.append(_event("known", "tr", event_id="k1"))
        store.append(_event("mystery", "tr", event_id="m1"))
        replayer = EventReplayer(store)
        with self.assertRaises(ReplayError) as ctx:
            replayer.replay_fold("tr", lambda acc, e: acc + 1, 0,
                                 known_names={"known"})
        self.assertIn("mystery", str(ctx.exception))
        self.assertIn("position 2", str(ctx.exception))

    def test_ignorable_unknown_skipped_and_ignore_policy(self):
        store = EventStore(":memory:")
        store.append(_event("known", "tr", event_id="k1"))
        store.append(_event("chatter", "tr", event_id="c1",
                            payload={"ignorable": True}))
        store.append(_event("known2", "tr", event_id="k2"))
        replayer = EventReplayer(store)
        # Ignorable desconhecido não derruba e não entra no fold.
        count = replayer.replay_fold("tr", lambda acc, e: acc + 1, 0,
                                     known_names={"known", "known2"})
        self.assertEqual(count, 2)
        # on_unknown='ignore' pula desconhecidos NÃO ignoráveis.
        store2 = EventStore(":memory:")
        store2.append(_event("x1", "tr2", event_id="x1"))
        store2.append(_event("boom", "tr2", event_id="b1"))
        replayer2 = EventReplayer(store2)
        count2 = replayer2.replay_fold("tr2", lambda acc, e: acc + 1, 0,
                                       known_names={"x1"}, on_unknown="ignore")
        self.assertEqual(count2, 1)


if __name__ == "__main__":
    unittest.main()
