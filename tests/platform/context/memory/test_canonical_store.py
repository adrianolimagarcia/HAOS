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


def test_duplicate_content_returns_original_canonical_identity(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    original = store.append(content="Canonical facts survive restart.", scope="project", idempotency_key="before-restart")
    replay = store.append(content="Canonical facts survive restart.", scope="project", idempotency_key="after-restart")
    assert replay.record_id == original.record_id
    store.close()


def test_search_fts_survives_untrusted_punctuation(tmp_path):
    """Chat/A2A text reaches MATCH raw: punctuation must not kill the prefetch.

    Regression: ``erro de espaco [btrfs]`` raised
    ``sqlite3.OperationalError: fts5: syntax error near "["`` and aborted the
    whole canonical prefetch for that turn.
    """
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    store.append(content="Erro de espaco em btrfs com subvolumes.", scope="project", idempotency_key="btrfs")

    hostile = ['erro de espaco [btrfs]', '[', ']', '(', ')', '*', 'a AND', 'a OR',
               'a NOT b', 'foo NEAR bar', 'unbalanced "quote', '""', '!!! ???', '^a', 'col:val']
    for query in hostile:
        assert isinstance(store.search_fts(query, ["project"]), list), query

    # the legitimate query still finds the record (recall preserved)
    hits = store.search_fts('erro de espaco [btrfs]', ["project"])
    assert [record.content for record in hits] == ["Erro de espaco em btrfs com subvolumes."]

    # diacritics folding and implicit-AND semantics preserved
    assert store.search_fts("espaco btrfs", ["project"])
    assert store.search_fts("btrfs subvolumes", ["project"])
    assert store.search_fts("inexistente", ["project"]) == []

    # punctuation-only queries yield nothing instead of raising
    assert store.search_fts("!!! ???", ["project"]) == []
    store.close()
