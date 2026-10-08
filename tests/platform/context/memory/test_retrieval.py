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


def test_render_freshness_observed_at_and_expired_soon(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    now = 1775500000.0  # Epoch timestamp de teste
    # hit1: observed_at presente, valid_until distante (> 7d)
    r1 = store.append(
        content="Alpha hit content",
        scope="project",
        idempotency_key="hit1",
        valid_from=now - 86400,
        observed_at=now - 86400,
    )
    # hit2: observed_at ausente, valid_until expirando em 2 dias (< 7d)
    r2 = store.append(
        content="Beta hit content",
        scope="project",
        idempotency_key="hit2",
        valid_from=now - 86400,
        observed_at=None,
    )
    with store._tx() as db:
        # Força observed_at como NULL explicitamente para hit2
        db.execute("UPDATE memory_records SET observed_at=NULL, valid_until=? WHERE record_id=?", (now + 2 * 86400, r2.record_id))
        db.execute("UPDATE memory_records SET valid_until=? WHERE record_id=?", (now + 10 * 86400, r1.record_id))
    retriever = HybridMemoryRetriever(store)
    ctx = retriever.format_context("content", ["project"], now=now)
    assert "observed=" in ctx
    assert "expired-soon" in ctx
    assert "freshness unknown" in ctx
    store.close()


def test_render_surfaces_detected_conflicts(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    # Dois fatos contraditórios para detect_conflicts identificar
    store.append(
        content="O servidor de banco de dados está online e ativo na porta 5432",
        scope="project",
        idempotency_key="conf1",
    )
    store.append(
        content="O servidor de banco de dados não está online e está inativo na porta 5432",
        scope="project",
        idempotency_key="conf2",
    )
    retriever = HybridMemoryRetriever(store)
    ctx = retriever.format_context("servidor banco dados porta 5432", ["project"])
    assert "[conflict:" in ctx
    store.close()


def test_empty_result_only_when_query_present(tmp_path):
    from hermes.platform.context.memory.provider import HermesFabricMemoryProvider
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    provider = HermesFabricMemoryProvider(canonical_store=store)
    provider.initialize(session_id="test_session")

    # Consulta que não retorna nada -> deve emitir mensagem explícita
    assert provider.prefetch("termo inexistente xyz") == "[nenhuma memória canônica encontrada para esta consulta]"

    # Sem query -> string vazia (sem poluição)
    assert provider.prefetch("") == ""
    assert provider.prefetch("   ") == ""
    store.close()


def test_format_context_budget_respected_with_header_metadata(tmp_path):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    now = 1775500000.0
    for i in range(5):
        store.append(
            content=f"Record content item {i} with some descriptive text payload",
            scope="project",
            idempotency_key=f"item_{i}",
            valid_from=now,
            observed_at=now,
        )
    retriever = HybridMemoryRetriever(store)
    # Testa com orçamento estrito de 300 caracteres
    budget = 300
    ctx = retriever.format_context("content item", ["project"], limit=5, budget_chars=budget, now=now)
    assert len(ctx) <= budget
    assert "[memory:" in ctx
    assert "observed=2026-" in ctx
    store.close()
