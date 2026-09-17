from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore

def test_idempotent_append_and_outbox_ack(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    first = store.append(content="Use SQLite as canonical memory.", scope="project", idempotency_key="req-1")
    again = store.append(content="Use SQLite as canonical memory.", scope="project", idempotency_key="req-1")
    assert again.record_id == first.record_id

    claimed = store.claim("obsidian", "worker-a")
    assert [event_id for event_id, _ in claimed] == ["memory.changed:req-1"]
    store.ack("memory.changed:req-1", "obsidian")
    assert store.claim("obsidian", "worker-b") == []
    store.close()

def test_supersession_hides_old_revision_from_retrieval(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    old = store.append(content="Provider A is enabled.", scope="project", idempotency_key="old")
    store.append(content="Provider B replaces Provider A.", scope="project", supersedes=[old.record_id], idempotency_key="new")
    ids = [record.record_id for record in store.search_fts("Provider", ["project"])]
    assert ids == ["new"]
    store.close()
