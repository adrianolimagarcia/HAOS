from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.retrieval import HybridMemoryRetriever

def test_retrieval_filters_scope_before_ranking(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    store.append(content="Private credential rotation procedure", scope="private", idempotency_key="private")
    store.append(content="Project release procedure", scope="project", idempotency_key="project")
    retriever = HybridMemoryRetriever(store)

    visible = retriever.retrieve("procedure", ["project"])
    assert [hit.record.record_id for hit in visible] == ["project"]
    assert "Private credential" not in retriever.format_context("procedure", ["project"])
    store.close()

def test_vector_ids_are_reauthorized_against_canonical_scope(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    store.append(content="private alpha", scope="private", idempotency_key="private")
    store.append(content="project beta", scope="project", idempotency_key="project")
    retriever = HybridMemoryRetriever(store, vector_search=lambda query, scopes, limit: ["private", "project"])

    assert [hit.record.record_id for hit in retriever.retrieve("missing", ["project"])] == ["project"]
    store.close()
