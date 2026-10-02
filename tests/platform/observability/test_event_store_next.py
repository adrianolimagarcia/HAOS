"""Real SQLite cursor batches preserve backlog and legacy tail semantics."""

import pytest

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event


def test_cursor_batches_preserve_backlog_and_new_writes(tmp_path):
    db_path = str(tmp_path / "events.db")
    with EventStore(db_path) as reader, EventStore(db_path) as writer:
        expected = []

        def append_events(count):
            for _ in range(count):
                index = len(expected)
                event = Event(
                    name="cursor.test",
                    event_id=f"event-{index}",
                    trace_id=f"trace-{index % 2}",
                    correlation_id="correlation",
                    causation_id="cause",
                    payload={"index": index},
                    # Sequence, not timestamp, determines the batch order.
                    timestamp=float(1000 - index),
                )
                writer.append(event)
                expected.append(event)

        append_events(150)
        first = reader.events_next(0)
        assert [event.event_id for event in first] == [event.event_id for event in expected[:64]]
        assert [event.seq for event in first] == list(range(1, 65))
        assert reader.events_next(0) == first
        assert reader.events_after(0, limit=64) == reader.events_after(0)[-64:]
        assert first != reader.events_after(0, limit=64)

        append_events(20)
        seen = first[:]
        cursor = first[-1].seq
        while batch := reader.events_next(cursor, limit=31):
            assert 1 <= len(batch) <= 31
            assert batch[0].seq > cursor
            assert [event.seq for event in batch] == sorted(event.seq for event in batch)
            seen.extend(batch)
            cursor = batch[-1].seq
        assert [event.event_id for event in seen] == [event.event_id for event in expected]
        assert [event.seq for event in seen] == list(range(1, len(expected) + 1))
        assert seen == expected  # Existing row conversion preserves event fields.
        assert reader.events_next(cursor) == []
        append_events(1)
        assert reader.events_next(cursor) == expected[-1:]
        assert reader.events_next(cursor)[0].seq == cursor + 1


def test_cursor_and_limit_boundaries(tmp_path):
    with EventStore(str(tmp_path / "events.db")) as store:
        assert store.events_next(0) == []
        for seq in (True, False, -1, 1.0, "1", None):
            with pytest.raises(ValueError, match="seq"):
                store.events_next(seq)
        for limit in (True, False, 0, -1, 257, 1.0, "1", None):
            with pytest.raises(ValueError, match="limit"):
                store.events_next(0, limit=limit)

        events = [Event(name="boundary.test", payload={"index": index}) for index in range(257)]
        for event in events:
            store.append(event)
        assert store.events_next(0, limit=1) == events[:1]
        assert store.events_next(0, limit=256) == events[:256]
        assert store.events_next(256, limit=256) == events[256:]
        assert store.events_next(257) == []
        assert store.events_next(258) == []
        assert store.events_next(2**100) == []
        assert store.events_after(0, limit=1) == events[-1:]
        assert store.events_after(1, limit=256) == events[1:]
