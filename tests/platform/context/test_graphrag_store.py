"""Item 3 — Persistência canônica do grafo GraphRAG em SQLite (ADR-008).

Contratos exercitados aqui (comportamento, não snapshots):
1. ``GraphRAGStore``: schema canônico (entities/relations/communities),
   WAL, idempotência por PK, caminho default ``$HERMES_HOME/memory/graphrag.db``.
2. ``IncrementalGraphRAGUpdater`` com ``store``: write-through — NOTE_CREATED
   upserta, NOTE_MODIFIED poda, NOTE_DELETED remove, supersession temporal
   ``superseded_by`` derivada das arestas ``supersedes``; com ``store=None`` o
   comportamento em memória continua intacto (nenhum arquivo criado).
3. Caminho real fechado (E2E): publicar KnowledgeEvent -> updater (store
   canônico default) -> ``GraphRAGClient`` (stack B, ``store_path``) devolve
   entidades; ``graphrag_query`` da toolset memory devolve sucesso com o grafo
   persistido.
4. ``FederatedMemoryCoordinator`` default grava no store canônico (o ponto de
   produção onde notas/ADRs viram KnowledgeEvent para o updater).
"""

import json
import sqlite3

import pytest

from hermes.platform.context.memory.events import (
    KnowledgeEvent,
    KnowledgeEventBus,
    KnowledgeEventType,
)
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.context.memory.graphrag import GraphRAGAdapter
from hermes.platform.context.memory.graphrag_store import (
    GraphRAGStore,
    default_graphrag_db_path,
)
from hermes.platform.context.memory.incremental_graphrag import IncrementalGraphRAGUpdater
from hermes.platform.memory.graphrag import GraphRAGClient, GraphRAGError

from tools.haos_memory_tools import graphrag_query

_ADR_CONTENT = (
    "# ADR-018: Protocol Fabric\n"
    "ProtocolAdapter depends on ModelResolver and connects to Dispatcher.\n"
    "See [[ANP-Spec]] for details.\n"
)


def _note_event(uri, title, content, event_type=KnowledgeEventType.NOTE_CREATED, metadata=None):
    return KnowledgeEvent.create(
        event_type=event_type,
        uri=uri,
        title=title,
        content=content,
        metadata=metadata or {},
    )


# --------------------------------------------------------------------------- #
# 1. GraphRAGStore — schema / WAL / idempotência / default path
# --------------------------------------------------------------------------- #
    def test_apply_event_persists_claims_and_is_idempotent(self, tmp_path):
        path = tmp_path / "graph.db"
        store = GraphRAGStore(path)
        assert store.apply_event("e1", "NOTE_CREATED", "obsidian://a", [("A", "service", "a")], [("A", "B", "calls", "ab")], scope="project")
        assert not store.apply_event("e1", "NOTE_CREATED", "obsidian://a", [("A", "service", "a")], [], scope="project")
        store.close()
        reopened = GraphRAGStore(path)
        assert len(reopened.list_entities()) == 1
        assert len(reopened.list_relations()) == 1
        assert reopened.apply_event("e2", "NOTE_DELETED", "obsidian://a", [], [], scope="project")
        assert reopened.list_entities() == []
        assert reopened.list_relations() == []
        reopened.close()

    def test_default_path_is_hermes_home_memory_graphrag_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # O default canônico precisa ser o MESMO para a stack A escrever e a
        # stack B ler: $HERMES_HOME/memory/graphrag.db.
        assert default_graphrag_db_path() == tmp_path / "memory" / "graphrag.db"

        store = GraphRAGStore()
        try:
            assert store.db_path == str(tmp_path / "memory" / "graphrag.db")
            assert (tmp_path / "memory" / "graphrag.db").exists()
            # WAL é o padrão do repo (event_store/kanban); :memory: não usa.
            conn = sqlite3.connect(store.db_path)
            try:
                assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
                # Schema canônico (entidades/arestas/comunidades) presente.
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            finally:
                conn.close()
            assert {"entities", "relations", "communities"} <= tables
        finally:
            store.close()

    def test_upsert_is_idempotent_by_pk(self, tmp_path):
        store = GraphRAGStore(tmp_path / "g.db")
        try:
            store.upsert_entity("AuthService", "Component", "first description")
            store.upsert_entity("AuthService", "Component", "second description")
            assert store.counts()["entities"] == 1
            assert store.get_entity("AuthService")["description"] == "second description"

            store.upsert_relation("AuthService", "DBStore", "calls", "v1")
            store.upsert_relation("AuthService", "DBStore", "calls", "v2")
            rels = store.list_relations()
            assert len(rels) == 1
            assert rels[0]["description"] == "v2"

            # Community merge é idempotente por conteúdo (não duplica no reprocessamento).
            store.upsert_community("Architecture", "includes AuthService.")
            store.upsert_community("Architecture", "includes AuthService.")
            comms = store.list_communities()
            assert len(comms) == 1
            assert comms[0]["summary"].count("includes AuthService.") == 1
        finally:
            store.close()

    def test_store_survives_reopen(self, tmp_path):
        """O grafo sobrevive a reinícios: fecha a conexão e reabre o arquivo."""
        path = tmp_path / "g.db"
        store = GraphRAGStore(path)
        store.upsert_entity("Kafka", "Technology", "messaging backbone")
        store.upsert_relation("Kafka", "AuthService", "used_by", "auth reads kafka topics")
        store.close()

        reopened = GraphRAGStore(path)
        try:
            assert reopened.get_entity("Kafka")["entity_type"] == "Technology"
            assert reopened.list_relations()[0]["target"] == "AuthService"
        finally:
            reopened.close()

    def test_search_matches_entity_and_description(self, tmp_path):
        store = GraphRAGStore(tmp_path / "g.db")
        try:
            store.upsert_entity("Kafka", "Technology", "distributed messaging backbone", "Architecture")
            store.upsert_entity("Postgres", "Technology", "sql storage engine", "Architecture")
            hits = store.search_entities(["backbone", "nope"])
            assert [h["entity"] for h in hits] == ["Kafka"]
            assert store.search_entities([]) == store.list_entities()
            # Entidade supersedida permanece legível com o ponteiro temporal.
            store.upsert_entity("ADR-001", "Decision", "old decision", "Architecture")
            store.mark_superseded("ADR-001", "ADR-002")
            assert store.get_entity("ADR-001")["superseded_by"] == "ADR-002"
        finally:
            store.close()


# --------------------------------------------------------------------------- #
# 2. Updater write-through (store None preserva comportamento em memória)
# --------------------------------------------------------------------------- #
class TestUpdaterPersistsToStore:
    def test_store_none_keeps_memory_only_behavior(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        bus = KnowledgeEventBus()
        graph = GraphRAGAdapter()
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, event_bus=bus)
        assert updater.store is None

        updater.process_event(_note_event("obsidian://20-Architecture/ADR-018.md",
                                          "ADR-018 Protocol Fabric", _ADR_CONTENT))
        # Adapter em memória atualizado e NENHUM arquivo de store criado.
        assert graph.query_local("ProtocolAdapter") is not None
        assert not (tmp_path / "memory").exists()

    def test_community_summary_merges_once_per_event(self, tmp_path):
        """Dois eventos da mesma comunidade somam; reprocessar não duplica."""
        graph = GraphRAGAdapter()
        store = GraphRAGStore(tmp_path / "g.db")
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            ev_a = _note_event("obsidian://20-Architecture/ADR-018.md",
                               "ADR-018 Protocol Fabric", _ADR_CONTENT)
            ev_b = _note_event("obsidian://20-Architecture/ADR-019.md",
                               "ADR-019 Auth Mesh",
                               "AuthMeshAdapter depends on TokenBroker.")
            updater.process_event(ev_a)
            updater.process_event(ev_a)  # reprocessamento não duplica no store
            updater.process_event(ev_b)
            comms = store.list_communities()
            assert len(comms) == 1
            summary = comms[0]["summary"]
            assert "Protocol Fabric" in summary or "ADR-018" in summary
            assert summary.count("includes entities such as") == 2
        finally:
            store.close()

    def test_note_created_upserts_store(self, tmp_path):
        graph = GraphRAGAdapter()
        store = GraphRAGStore(tmp_path / "g.db")
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            updater.process_event(_note_event("obsidian://20-Architecture/ADR-018.md",
                                              "ADR-018 Protocol Fabric", _ADR_CONTENT))
            counts = store.counts()
            assert counts["entities"] > 0
            assert counts["relations"] > 0
            assert counts["communities"] >= 1
            # A entidade extraída do evento existe tanto no adapter quanto no store.
            ent = store.get_entity("ProtocolAdapter")
            assert ent is not None
            assert ent["entity_type"] == "Component"
            assert graph.query_local("ProtocolAdapter") is not None
        finally:
            store.close()

    def test_note_deleted_removes_uri_items_only(self, tmp_path):
        graph = GraphRAGAdapter()
        store = GraphRAGStore(tmp_path / "g.db")
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            uri_a = "obsidian://20-Architecture/ADR-100.md"
            uri_b = "obsidian://20-Architecture/ADR-200.md"
            updater.process_event(_note_event(
                uri_a, "ADR-100 Legacy Auth", "LegacyAuthAdapter depends on OldSessionStore."))
            updater.process_event(_note_event(
                uri_b, "ADR-200 Modern Auth", "ModernAuthAdapter depends on NewSessionStore."))
            assert store.counts()["entities"] >= 4

            # Deletar A remove só as entidades que A alegava em exclusivo.
            updater.process_event(_note_event(uri_a, "", "",
                                              event_type=KnowledgeEventType.NOTE_DELETED))
            names = {e["entity"] for e in store.list_entities()}
            assert "LegacyAuthAdapter" not in names
            assert "OldSessionStore" not in names
            assert "ModernAuthAdapter" in names
            assert "NewSessionStore" in names
            rel_sources = {r["source"] for r in store.list_relations()}
            assert "LegacyAuthAdapter" not in rel_sources
        finally:
            store.close()

    def test_note_modified_prunes_dropped_entity(self, tmp_path):
        graph = GraphRAGAdapter()
        store = GraphRAGStore(tmp_path / "g.db")
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            uri = "obsidian://30-Workflows/runbook-1.md"
            updater.process_event(_note_event(uri, "Runbook 1",
                                              "PipelineWorker calls LegacyCleaner."))
            assert store.get_entity("LegacyCleaner") is not None

            # A mesma URI agora não menciona mais LegacyCleaner: a entidade é
            # podada (ninguém mais a alega) e some do store.
            updater.process_event(_note_event(uri, "Runbook 1",
                                              "PipelineWorker calls NewCleaner.",
                                              event_type=KnowledgeEventType.NOTE_MODIFIED))
            assert store.get_entity("LegacyCleaner") is None
            assert store.get_entity("NewCleaner") is not None
        finally:
            store.close()

    def test_supersession_marks_superseded_by(self, tmp_path):
        graph = GraphRAGAdapter()
        store = GraphRAGStore(tmp_path / "g.db")
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            updater.process_event(_note_event(
                "obsidian://20-Architecture/ADR-001.md", "ADR-001 Legacy",
                "LegacyAuthAdapter depends on OldStore.", metadata={"id": "ADR-001"}))
            updater.process_event(_note_event(
                "obsidian://20-Architecture/ADR-002.md", "ADR-002 Modern",
                "ModernAuthAdapter depends on NewStore.",
                metadata={"id": "ADR-002", "supersedes": ["ADR-001"]}))
            # Supersessão temporal (ADR-008): o antigo aponta para o novo.
            assert store.get_entity("ADR-001")["superseded_by"] == "ADR-002"
        finally:
            store.close()


# --------------------------------------------------------------------------- #
# 3. E2E: KnowledgeEvent -> updater -> store canônico -> GraphRAGClient/tool
# --------------------------------------------------------------------------- #
def _populate_default_store(tmp_path, monkeypatch):
    """HERMES_HOME isolado + updater no store canônico default processando um ADR."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    bus = KnowledgeEventBus()
    graph = GraphRAGAdapter()
    store = GraphRAGStore()
    updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, event_bus=bus, store=store)
    updater.process_event(_note_event(
        "obsidian://20-Architecture/ADR-018.md", "ADR-018 Protocol Fabric", _ADR_CONTENT))
    store.close()
    return graph


def test_e2e_updater_store_then_graphrag_client_reads(tmp_path, monkeypatch):
    _populate_default_store(tmp_path, monkeypatch)
    db = str(default_graphrag_db_path())
    assert GraphRAGStore(db).counts()["entities"] > 0

    # Stack B: um NOVO processo/instância lê o mesmo arquivo canônico.
    client = GraphRAGClient(store_path=db)
    assert client.available()
    assert client.mode == "sqlite"
    out = client.query_local("qual adapter de protocolo?")
    assert out["mode"] == "sqlite"
    names = {e["entity"] for e in out["entities"]}
    assert "ProtocolAdapter" in names
    assert any(r["source"] == "ProtocolAdapter" or r["target"] == "ProtocolAdapter"
               for r in out["relationships"])


def test_e2e_graphrag_query_tool_returns_real_results(tmp_path, monkeypatch):
    _populate_default_store(tmp_path, monkeypatch)
    # graphrag_query lê o store canônico default (sem CSV feito à mão).
    payload = json.loads(graphrag_query("qual adapter de protocolo?", mode="local"))
    assert payload["success"] is True
    results = payload["results"]
    assert results["mode"] == "sqlite"
    names = {e["entity"] for e in results["entities"]}
    assert "ProtocolAdapter" in names

    # Fail-closed preservado: sem store e sem CSV -> erro da tool.
    empty = tmp_path / "empty-home"
    empty.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(empty))
    payload = json.loads(graphrag_query("qualquer coisa", mode="global"))
    assert "success" not in payload
    assert "não disponível" in payload["error"]


def test_e2e_federated_coordinator_writes_canonical_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    coordinator = FederatedMemoryCoordinator(vault_path=str(tmp_path / "vault"))
    try:
        # Ponto de produção: ingest de fato/ADR publica KnowledgeEvent que o
        # updater (store-backed) consome; o grafo persiste no store canônico.
        coordinator.ingest_candidate_fact(
            fact="ADR-300: Adopt Event-Driven Architecture with Kafka as messaging backbone.",
            scope="project",
            provenance="architecture/adr-300.md",
            confidence=0.99,
            metadata={"title": "ADR-300: Kafka Backbone"},
        )
        assert coordinator.graphrag_store.counts()["entities"] > 0
        assert (tmp_path / "memory" / "graphrag.db").exists()

        payload = json.loads(graphrag_query("qual backbone de mensageria?", mode="local"))
        assert payload["success"] is True
        names = {e["entity"] for e in payload["results"]["entities"]}
        assert "Kafka" in names
    finally:
        coordinator.close()


def test_coordinator_close_does_not_close_injected_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = GraphRAGStore(tmp_path / "injected.db")
    coordinator = FederatedMemoryCoordinator(
        vault_path=str(tmp_path / "vault"),
        graphrag_store=store,
    )
    coordinator.close()
    # Store injetado pertence ao chamador: segue utilizável após o close.
    store.upsert_entity("External", "Component", "still open")
    assert store.get_entity("External") is not None
    store.close()


# --------------------------------------------------------------------------- #
# 4. GraphRAGClient sqlite mode — fail-closed
# --------------------------------------------------------------------------- #
class TestGraphRAGClientSqliteMode:
    def test_constructor_still_fails_closed_without_mode(self):
        with pytest.raises(GraphRAGError):
            GraphRAGClient()

    def test_missing_store_file_fails_closed(self, tmp_path):
        client = GraphRAGClient(store_path=str(tmp_path / "missing.db"))
        assert not client.available()
        with pytest.raises(GraphRAGError):
            client.query_local("kafka")
