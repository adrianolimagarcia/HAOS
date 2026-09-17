import threading
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.projection_runner import ProjectionRunner

def test_each_projection_is_independently_acknowledged(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    store.append(content="memory projection test", scope="project", idempotency_key="one")
    applied = []
    runner = ProjectionRunner(store, {name: lambda record, name=name: applied.append((name, record.record_id)) for name in store.PROJECTIONS})
    assert runner.drain() == len(store.PROJECTIONS)
    assert sorted(name for name, _ in applied) == sorted(store.PROJECTIONS)
    assert runner.drain() == 0
    store.close()

def test_failed_projection_remains_retryable(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    store.append(content="retryable record", scope="project", idempotency_key="retry")
    calls = {"graph": 0}
    def graph(record):
        calls["graph"] += 1
        if calls["graph"] == 1:
            raise RuntimeError("transient")
    projectors = {name: (lambda record: None) for name in store.PROJECTIONS}
    projectors["graphrag"] = graph
    runner = ProjectionRunner(store, projectors)
    runner.drain()
    assert calls["graph"] == 1
    # Make the test deterministic without waiting for retry backoff.
    store.fail("memory.changed:retry", "graphrag", "retry-now", retry_after=0)
    runner.drain()
    assert calls["graph"] == 2
    store.close()

def test_concurrent_idempotent_writes(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    threads = [threading.Thread(target=lambda: store.append(content="same fact", scope="project", idempotency_key="shared")) for _ in range(8)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len(store.search_fts("same", ["project"])) == 1
    store.close()
