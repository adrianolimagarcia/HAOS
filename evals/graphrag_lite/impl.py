"""Implementacao dos casos do gold set graphrag-lite.

Cada funcao `case_<id>` e uma pergunta de avaliacao: entrada deterministica +
contrato de comportamento verificavel contra a maquinaria real. Uma violacao
levanta ``CaseFailure`` com a mensagem; o PASS imprime uma linha de detalhe.

Regras do gold set:
- fixture sempre em tempfile (nunca ~/.hermes); HERMES_HOME do runner isola o caso.
- zero rede; stdlib + modulos do repo.
- nenhum teste le o texto do codigo-fonte; nenhum change-detector.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from urllib.error import URLError
from unittest import mock


class CaseFailure(Exception):
    """Contrato violado — o caso falhou."""


def fail(msg: str) -> "None":
    raise CaseFailure(msg)


def _store_dir() -> "tuple[tempfile.TemporaryDirectory[str], Path]":
    td = tempfile.TemporaryDirectory()
    return td, Path(td.name)


def _note_event(uri, title, content, event_type=None, metadata=None):
    from hermes.platform.context.memory.events import (
        KnowledgeEvent,
        KnowledgeEventType,
    )

    return KnowledgeEvent.create(
        event_type=event_type or KnowledgeEventType.NOTE_CREATED,
        uri=uri,
        title=title,
        content=content,
        metadata=metadata or {},
    )


def _seed_store(db_path: Path, events) -> "object":
    from hermes.platform.context.memory.graphrag import GraphRAGAdapter
    from hermes.platform.context.memory.graphrag_store import GraphRAGStore
    from hermes.platform.context.memory.incremental_graphrag import (
        IncrementalGraphRAGUpdater,
    )

    graph = GraphRAGAdapter()
    store = GraphRAGStore(db_path)
    updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
    for ev in events:
        updater.process_event(ev)
    store.close()
    return graph


_ADR_018 = (
    "# ADR-018: Protocol Fabric\n"
    "Uses Kafka as messaging backbone.\n"
    "ProtocolAdapter depends on ModelResolver.\n"
    "ProtocolAdapter connects to Dispatcher.\n"
    "See [[ANP-Spec]] for details.\n"
)


# --------------------------------------------------------------------------- #
# A. Extracao de fato para o grafo
# --------------------------------------------------------------------------- #
def case_graphrag_extrai_relacoes_linha():
    """Pergunta: o grafo persiste relacoes extraidas de linhas do texto?"""
    td, base = _store_dir()
    try:
        _seed_store(base / "g.db", [
            _note_event("obsidian://20-Architecture/ADR-018.md",
                        "ADR-018 Protocol Fabric", _ADR_018),
        ])
        from hermes.platform.context.memory.graphrag_store import GraphRAGStore

        store = GraphRAGStore(base / "g.db")
        try:
            rels = {(r["source"], r["relation_type"], r["target"])
                    for r in store.list_relations()}
        finally:
            store.close()
        want = {
            ("ProtocolAdapter", "depends_on", "ModelResolver"),
            ("ProtocolAdapter", "connects_to", "Dispatcher"),
        }
        if not want <= rels:
            fail(f"relacoes de linha ausentes: {sorted(want - rels)} "
                 f"(presentes={sorted(rels)})")
        print(f"PASS: {sorted(want)} persistidas no store")
    finally:
        td.cleanup()


def case_graphrag_extrai_entidades():
    """Pergunta: componentes, tecnologias, wikilinks e ADRs viram entidades?"""
    td, base = _store_dir()
    try:
        _seed_store(base / "g.db", [
            _note_event("obsidian://20-Architecture/ADR-018.md",
                        "ADR-018 Protocol Fabric", _ADR_018),
        ])
        from hermes.platform.context.memory.graphrag_store import GraphRAGStore

        store = GraphRAGStore(base / "g.db")
        try:
            kinds = {e["entity"]: e["entity_type"] for e in store.list_entities()}
        finally:
            store.close()
        want = {
            "ProtocolAdapter": "Component",
            "Kafka": "Technology",
            "ANP-Spec": "Concept",
            "ADR-018": "ADR",
        }
        bad = {k: (kinds.get(k), v) for k, v in want.items() if kinds.get(k) != v}
        if bad:
            fail(f"tipos de entidade errados: {bad}")
        print(f"PASS: {len(want)} entidades com tipo esperado")
    finally:
        td.cleanup()


def case_graphrag_extrai_idempotente():
    """Pergunta: reprocessar o mesmo fato nao duplica; deletar remove so o proprio."""
    td, base = _store_dir()
    try:
        from hermes.platform.context.memory.graphrag import GraphRAGAdapter
        from hermes.platform.context.memory.graphrag_store import GraphRAGStore
        from hermes.platform.context.memory.incremental_graphrag import (
            IncrementalGraphRAGUpdater,
        )
        from hermes.platform.context.memory.events import KnowledgeEventType

        db = base / "g.db"
        graph = GraphRAGAdapter()
        store = GraphRAGStore(db)
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            ev_a = _note_event("obsidian://20-Architecture/ADR-100.md",
                               "ADR-100 Legacy",
                               "LegacyAuthAdapter depends on OldSessionStore.")
            ev_b = _note_event("obsidian://20-Architecture/ADR-200.md",
                               "ADR-200 Modern",
                               "ModernAuthAdapter depends on NewSessionStore.")
            updater.process_event(ev_a)
            first = store.counts()
            updater.process_event(ev_a)  # reprocessamento
            second = store.counts()
            if first != second:
                fail(f"reprocessar mudou contagens: {first} -> {second}")
            updater.process_event(ev_b)
            updater.process_event(_note_event(
                "obsidian://20-Architecture/ADR-100.md", "", "",
                event_type=KnowledgeEventType.NOTE_DELETED))
            names = {e["entity"] for e in store.list_entities()}
            if "LegacyAuthAdapter" in names or "OldSessionStore" in names:
                fail("delecao nao removeu itens exclusivos do ADR-100")
            if "ModernAuthAdapter" not in names or "NewSessionStore" not in names:
                fail("delecao do ADR-100 removeu itens do ADR-200")
        finally:
            store.close()
        print("PASS: upsert idempotente; NOTE_DELETED escopada por URI")
    finally:
        td.cleanup()


def case_graphrag_extrai_supersessao():
    """Pergunta: um fato que supersede outro marca o ponteiro temporal (ADR-008)?"""
    td, base = _store_dir()
    try:
        from hermes.platform.context.memory.graphrag_store import GraphRAGStore

        _seed_store(base / "g.db", [
            _note_event("obsidian://20-Architecture/ADR-001.md", "ADR-001 Legacy",
                        "LegacyAuthAdapter depends on OldStore.",
                        metadata={"id": "ADR-001"}),
            _note_event("obsidian://20-Architecture/ADR-002.md", "ADR-002 Modern",
                        "ModernAuthAdapter depends on NewStore.",
                        metadata={"id": "ADR-002", "supersedes": ["ADR-001"]}),
        ])
        store = GraphRAGStore(base / "g.db")
        try:
            old = store.get_entity("ADR-001")
            new = store.get_entity("ADR-002")
        finally:
            store.close()
        if old is None or new is None:
            fail("entidades ADR-001/ADR-002 ausentes do store")
        if old.get("superseded_by") != "ADR-002":
            fail(f"superseded_by={old.get('superseded_by')!r} != 'ADR-002'")
        print("PASS: superseded_by aponta ADR-001 -> ADR-002")
    finally:
        td.cleanup()


def case_graphrag_extrai_modificado_poda():
    """Pergunta: editar uma nota remove a entidade que ela deixou de citar?"""
    td, base = _store_dir()
    try:
        from hermes.platform.context.memory.graphrag import GraphRAGAdapter
        from hermes.platform.context.memory.graphrag_store import GraphRAGStore
        from hermes.platform.context.memory.incremental_graphrag import (
            IncrementalGraphRAGUpdater,
        )
        from hermes.platform.context.memory.events import KnowledgeEventType

        db = base / "g.db"
        graph = GraphRAGAdapter()
        store = GraphRAGStore(db)
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            uri = "obsidian://30-Workflows/runbook-1.md"
            updater.process_event(_note_event(uri, "Runbook 1",
                                              "PipelineWorker calls LegacyCleaner."))
            updater.process_event(_note_event(
                uri, "Runbook 1", "PipelineWorker calls NewCleaner.",
                event_type=KnowledgeEventType.NOTE_MODIFIED))
            names = {e["entity"] for e in store.list_entities()}
            if "LegacyCleaner" in names:
                fail("NOTE_MODIFIED nao podou a entidade que a URI deixou de citar")
            if "NewCleaner" not in names:
                fail("NOTE_MODIFIED nao persistiu a entidade nova")
        finally:
            store.close()
        print("PASS: poda por URI na modificacao (LegacyCleaner fora, NewCleaner dentro)")
    finally:
        td.cleanup()


# --------------------------------------------------------------------------- #
# B. Recuperacao por similaridade semantica (busca por termos sobre nome/descricao)
# --------------------------------------------------------------------------- #
def case_graphrag_recall_semantico():
    """Pergunta: um termo que so existe na DESCRICAO alcanca a entidade?"""
    td, base = _store_dir()
    try:
        _seed_store(base / "g.db", [
            _note_event("obsidian://20-Architecture/ADR-018.md",
                        "ADR-018 Protocol Fabric", _ADR_018),
        ])
        from hermes.platform.memory.graphrag import GraphRAGClient

        client = GraphRAGClient(store_path=str(base / "g.db"))
        out = client.query_local("qual o backbone de mensageria?")
        names = {e["entity"] for e in out["entities"]}
        # 'backbone' so existe na descricao da entidade primaria (a descricao
        # trunca em 150 chars — contrato real do updater; o termo foi posto cedo).
        if "ADR-018 Protocol Fabric" not in names:
            fail(f"termo de descricao nao alcancou a nota: {sorted(names)}")
        print(f"PASS: recall por descricao ({sorted(names)})")
    finally:
        td.cleanup()


def case_graphrag_recall_fallback_termos_curtos():
    """Pergunta: pergunta so com palavras curtas nao quebra — cai no fallback?"""
    td, base = _store_dir()
    try:
        _seed_store(base / "g.db", [
            _note_event("obsidian://20-Architecture/ADR-018.md",
                        "ADR-018 Protocol Fabric", _ADR_018),
        ])
        from hermes.platform.context.memory.graphrag_store import GraphRAGStore
        from hermes.platform.memory.graphrag import GraphRAGClient

        store = GraphRAGStore(base / "g.db")
        try:
            n_total = store.counts()["entities"]
        finally:
            store.close()
        client = GraphRAGClient(store_path=str(base / "g.db"))
        out = client.query_local("de o em")  # todos <= 2 chars -> list_entities()
        if len(out["entities"]) != n_total:
            fail(f"fallback devolveu {len(out['entities'])} entidades, esperado {n_total}")
        print(f"PASS: fallback list_entities ({n_total} entidades) sem quebrar")
    finally:
        td.cleanup()


def case_graphrag_recall_um_salto():
    """Pergunta: a consulta devolve a entidade e as relacoes que a tocam (1 salto)?"""
    td, base = _store_dir()
    try:
        _seed_store(base / "g.db", [
            _note_event("obsidian://20-Architecture/ADR-030.md", "ADR-030 Chain",
                        "AlphaService depends on BetaStore.\n"
                        "BetaStore connects to GammaQueue."),
        ])
        from hermes.platform.memory.graphrag import GraphRAGClient

        client = GraphRAGClient(store_path=str(base / "g.db"))
        out = client.query_local("AlphaService")
        names = {e["entity"] for e in out["entities"]}
        if "AlphaService" not in names:
            fail(f"AlphaService nao retornado: {sorted(names)}")
        rel_keys = {(r["source"], r["relation_type"], r["target"])
                    for r in out["relationships"]}
        if ("AlphaService", "depends_on", "BetaStore") not in rel_keys:
            fail(f"relacao de 1 salto ausente: {sorted(rel_keys)}")
        # Relacao que NAO toca a entidade casada nao vaza (GammaQueue fica de fora).
        if any(r["source"] == "BetaStore" and r["target"] == "GammaQueue"
               for r in out["relationships"]):
            fail("relacao de 2o salto vazou na consulta de 1 salto")
        # method ecoado no resultado (contrato da API do client).
        if out.get("method") != "local":
            fail(f"method={out.get('method')!r} != 'local'")
        g2 = client.query_global("AlphaService")
        if g2.get("method") != "global":
            fail(f"method={g2.get('method')!r} != 'global' na query_global")
        print("PASS: 1 salto (entidade + relacoes tocantes), method ecoado")
    finally:
        td.cleanup()


# --------------------------------------------------------------------------- #
# C. Recuperacao por grafo (multihop)
# --------------------------------------------------------------------------- #
def case_graphrag_multihop_dois_saltos():
    """Pergunta: cadeia A->B->C e alcancavel em 2 consultas encadeadas?"""
    td, base = _store_dir()
    try:
        _seed_store(base / "g.db", [
            _note_event("obsidian://20-Architecture/ADR-030.md", "ADR-030 Chain",
                        "AlphaService depends on BetaStore.\n"
                        "BetaStore connects to GammaQueue."),
        ])
        from hermes.platform.memory.graphrag import GraphRAGClient

        client = GraphRAGClient(store_path=str(base / "g.db"))
        hop1 = client.query_local("AlphaService")
        hop1_names = {e["entity"] for e in hop1["entities"]}
        hop1_rels = {(r["source"], r["relation_type"], r["target"])
                     for r in hop1["relationships"]}
        if ("AlphaService", "depends_on", "BetaStore") not in hop1_rels:
            fail(f"salto 1: relacao AlphaService->BetaStore ausente: {sorted(hop1_rels)}")
        if "GammaQueue" in hop1_names:
            fail("salto 1 ja alcancou GammaQueue (client devolve 1 salto por chamada)")

        hop2 = client.query_local("BetaStore")
        hop2_rels = {(r["source"], r["relation_type"], r["target"])
                     for r in hop2["relationships"]}
        if ("BetaStore", "connects_to", "GammaQueue") not in hop2_rels:
            fail(f"salto 2: relacao BetaStore->GammaQueue ausente: {sorted(hop2_rels)}")
        # Fechamento: o alvo do 2o salto e alcancavel encadeando chamadas reais.
        print("PASS: 2 saltos encadeados (AlphaService -> BetaStore -> GammaQueue)")
    finally:
        td.cleanup()


def case_graphrag_multihop_alvo_de_aresta():
    """Pergunta: entidade que so aparece como ALVO de aresta e descobrivel?"""
    td, base = _store_dir()
    try:
        _seed_store(base / "g.db", [
            _note_event("obsidian://20-Architecture/ADR-040.md", "ADR-040 Front",
                        "Frontend calls ApiGateway."),
        ])
        from hermes.platform.memory.graphrag import GraphRAGClient

        client = GraphRAGClient(store_path=str(base / "g.db"))
        out = client.query_local("ApiGateway")
        names = {e["entity"] for e in out["entities"]}
        if "ApiGateway" not in names:
            fail(f"alvo de aresta nao recuperado: {sorted(names)}")
        rels = {(r["source"], r["relation_type"], r["target"])
                for r in out["relationships"]}
        if ("Frontend", "calls", "ApiGateway") not in rels:
            fail(f"aresta entrante nao superficia a origem: {sorted(rels)}")
        print("PASS: alvo de aresta recuperado com a origem visivel na relacao")
    finally:
        td.cleanup()


def case_graphrag_multihop_comunidade_drift():
    """Pergunta: consulta DRIFT expande a entidade com o relatorio da comunidade?"""
    from hermes.platform.context.memory.graphrag import GraphRAGAdapter

    graph = GraphRAGAdapter()
    graph.register_entity("AuthMesh", "Component",
                          "mesh de autenticacao", community="Architecture")
    graph.register_community_report(
        "Architecture",
        "Themes: Kafka backbone, drift context for the auth mesh.")
    drift = graph.query_drift("AuthMesh")
    types = {it.item_type for it in drift}
    if "dependency_graph" not in types or "holistic_community_report" not in types:
        fail(f"DRIFT nao devolveu local+comunidade: {sorted(types)}")
    g = graph.query_global("kafka")  # substring filter do query_global
    if len(g) != 1 or g[0].item_type != "holistic_community_report":
        fail(f"query_global('kafka') devolveu {len(g)} itens, esperado 1")
    if graph.query_global("zzz-nao-existe"):
        fail("query_global casou comunidade sem o termo")
    print("PASS: DRIFT local+comunidade; global filtra por substring")
    return None


# --------------------------------------------------------------------------- #
# D. Rerank (RRF) e fusao de rankings
# --------------------------------------------------------------------------- #
def case_ragflow_rrf_contrato():
    """Pergunta: RRF premia presenca em mais listas e so seleciona do pool?"""
    from hermes.platform.memory.ragflow_engine import ReciprocalRankFusion as RRF

    k = 60
    r1 = [("a", 1.0), ("b", 0.5)]
    r2 = [("b", 2.0), ("a", 1.0)]
    r3 = [("c", 9.0)]  # presente em UMA lista so
    fused = RRF.fuse([r1, r2, r3], k=k)
    got = dict(fused)
    # Forma fechada do RRF (w/(k+rank) por lista): a e b aparecem nas duas
    # listas (rank1 numa, rank2 noutra); c so numa.
    ab_score = 1 / (k + 1) + 1 / (k + 2)
    c_score = 1 / (k + 1)
    if abs(got["a"] - ab_score) > 1e-9 or abs(got["b"] - ab_score) > 1e-9:
        fail(f"score RRF divergente: a={got['a']} b={got['b']} fechado={ab_score}")
    if abs(got["c"] - c_score) > 1e-9:
        fail(f"score c={got['c']} != {c_score}")
    # Presenca em 2 listas vence presenca em 1 (contrato do ADR-002).
    if not (got["a"] > got["c"]):
        fail(f"presenca multipla nao venceu: a={got['a']} c={got['c']}")
    # Nada fora do pool: os ids fundidos sao exatamente os ids de entrada.
    if set(got) != {"a", "b", "c"}:
        fail(f"selecionados fora do pool: {set(got) ^ {'a', 'b', 'c'}}")
    # Determinismo: mesma entrada -> mesma saida.
    again = RRF.fuse([r1, r2, r3], k=k)
    if again != fused:
        fail("RRF nao deterministico entre execucoes")
    print("PASS: RRF formula, presenca multipla, pool fechado, deterministico")
    return None


def case_ragflow_rrf_pesos():
    """Pergunta: pesos reordenam a fusao (lista de maior peso domina)?"""
    from hermes.platform.memory.ragflow_engine import ReciprocalRankFusion as RRF

    fused = RRF.fuse(
        [[("a", 1.0), ("b", 1.0)], [("a", 1.0), ("c", 2.0)]],
        k=60, weights=[10.0, 1.0],
    )
    order = [item for item, _ in fused]
    if order != ["a", "b", "c"]:
        fail(f"ordem com pesos {order!r} != ['a','b','c']")
    # a = 10/61 (lista 1, rank 1) + 1/61 (lista 2, rank 1) = 11/61.
    if abs(dict(fused)["a"] - 11 / 61) > 1e-9:
        fail(f"score de 'a' com pesos divergente: {dict(fused)['a']}")
    print("PASS: pesos reordenam (a > b > c), score fechado 11/61")
    return None


def case_ragflow_hybrid_frase_exata():
    """Pergunta: chunk com a frase exata da pergunta vence o overlap parcial?"""
    td, base = _store_dir()
    try:
        from hermes.platform.memory.ragflow_engine import RAGFlowStore

        store = RAGFlowStore(base / "rag.db")
        store.index_document("doc-a.md",
                             "# Alpha\nProtocolAdapter depends on ModelResolver.",
                             doc_id="doc-a")
        store.index_document("doc-b.md",
                             "# Beta\nProtocolAdapter is mentioned but the phrase differs.",
                             doc_id="doc-b")
        q = "ProtocolAdapter depends on ModelResolver"
        r1 = store.hybrid_search(q, limit=2)
        r2 = store.hybrid_search(q, limit=2)
        if not r1 or r1[0].doc_path != "doc-a.md":
            fail(f"frase exata nao venceu: {[c.doc_path for c in r1]}")
        if len(r1) > 2:
            fail(f"limit desrespeitado: {len(r1)}")
        if [c.doc_path for c in r1] != [c.doc_path for c in r2]:
            fail("hybrid_search nao deterministico")
        if store.hybrid_search("") != []:
            fail("hybrid_search('') nao devolveu lista vazia")
        print(f"PASS: frase exata em primeiro ({r1[0].doc_path})")
    finally:
        td.cleanup()


# --------------------------------------------------------------------------- #
# E. Fusao hibrida (HybridKnowledgeRouter)
# --------------------------------------------------------------------------- #
def _router(okf_dir: Path, base: Path, graphrag_dir: "Path | None"):
    from hermes.platform.memory.hybrid_router import HybridKnowledgeRouter
    from hermes.platform.memory.ragflow_engine import RAGFlowStore

    return HybridKnowledgeRouter(
        okf_dir=okf_dir,
        graphrag_dir=graphrag_dir,
        reconciler_db_path=base / "rec.db",
        ragflow_store=RAGFlowStore(base / "rag.db"),
    )


def case_hibrido_okf_deterministico():
    """Pergunta: o contrato OKF vence a busca probabilistica quando casa?"""
    td, base = _store_dir()
    try:
        from hermes.platform.memory.okf import OKFStore

        okf = OKFStore(base / "okf")
        okf.save_document(title="Payment Endpoint Contract",
                          content="POST /api/v1/payments requires "
                                  "Authorization: Bearer <token>",
                          doc_type="api-contract", tags=["payments", "api"])
        # Grafo com entidade que TAMBEM casaria por termo (prova de precedencia:
        # OKF deterministico vence o probabilistico mesmo quando ambos casam).
        gdir = base / "graphrag"
        gdir.mkdir()
        (gdir / "entities.csv").write_text(
            "entity,type,description\n"
            "PaymentEndpoint,Component,endpoint de pagamento\n"
            "GraphRAGClient,Component,client do grafo\n",
            encoding="utf-8")
        (gdir / "relationships.csv").write_text(
            "source,target,relation_type,description\n"
            "PaymentEndpoint,ApiGateway,calls,x\n"
            "GraphRAGClient,HybridRouter,depends_on,x\n",
            encoding="utf-8")
        router = _router(okf_dir=base / "okf", base=base, graphrag_dir=gdir)
        r = router.query("Payment Endpoint Contract")
        if r["source"] != "OKF_CANONICAL" or r["deterministic"] is not True:
            fail(f"precedencia: source={r['source']!r}")
        # Termo que so o grafo conhece: cai no probabilistico com a entidade.
        r2 = router.query("GraphRAGClient")
        if r2["source"] != "RAG_PROBABILISTIC":
            fail(f"fallback probabilistico: source={r2['source']!r}")
        ent_names = {e["entity"] for e in (r2.get("results") or {}).get("entities", [])}
        if "GraphRAGClient" not in ent_names:
            fail(f"entidade do grafo ausente no fallback: {sorted(ent_names)}")
        print("PASS: OKF deterministico antes do probabilistico")
    finally:
        td.cleanup()


def case_hibrido_memoria_reconciliada():
    """Pergunta: memoria reconciliada ativa vence o OKF e esconde supersedidas?"""
    td, base = _store_dir()
    try:
        from hermes.platform.memory.okf import OKFStore

        okf = OKFStore(base / "okf")
        okf.save_document(title="Payment Endpoint Contract",
                          content="POST /api/v1/payments requires "
                                  "Authorization: Bearer <token>",
                          doc_type="api-contract", tags=["payments", "api"])
        router = _router(okf_dir=base / "okf", base=base, graphrag_dir=None)
        router.reconcile_memory(topic="payment endpoint contract",
                                content="The canonical endpoint moved to /api/v2/payments.",
                                category="api")
        r = router.query("Payment Endpoint Contract")
        if r["source"] != "RECONCILED_MEMORY":
            fail(f"memoria reconciliada nao venceu o OKF: source={r['source']!r}")
        if "/api/v2/payments" not in r["content"]:
            fail("conteudo da memoria reconciliada ausente da resposta")
        # Supersessao: fato novo substitui; o antigo nao vaza.
        router.reconcile_memory(
            topic="payment endpoint contract",
            content="nao usamos mais /api/v2/payments, agora usamos /api/v3/payments",
            category="api")
        active = router.get_active_memories(topic="payment endpoint contract")
        if len(active) != 1 or "/api/v3/payments" not in active[0].content:
            fail(f"ativo apos supersede inesperado: {[m.content for m in active]}")
        r2 = router.query("Payment Endpoint Contract")
        if "/api/v3/payments" not in r2["content"]:
            fail("router devolveu fato supersedido")
        print("PASS: memoria ativa vence; supersedida nunca e devolvida")
    finally:
        td.cleanup()


def case_hibrido_fallback_graphrag():
    """Pergunta: sem OKF, o grafo responde; mode okf ignora o grafo; sem grafo nao quebra?"""
    td, base = _store_dir()
    try:
        from hermes.platform.memory.okf import OKFStore

        okf = OKFStore(base / "okf")
        okf.save_document(title="Contrato Pagamentos",
                          content="Authorization Bearer token flow for the vault.",
                          doc_type="api-contract", tags=["payments"])
        gdir = base / "graphrag"
        gdir.mkdir()
        (gdir / "entities.csv").write_text(
            "entity,type,description\nGraphRAGClient,Component,client do grafo\n",
            encoding="utf-8")
        (gdir / "relationships.csv").write_text(
            "source,target,relation_type,description\nGraphRAGClient,HybridRouter,depends_on,x\n",
            encoding="utf-8")
        router = _router(okf_dir=base / "okf", base=base, graphrag_dir=gdir)
        r = router.query("GraphRAGClient")
        if r["source"] != "RAG_PROBABILISTIC":
            fail(f"fallback graphrag: source={r['source']!r}")
        ent_names = {e["entity"] for e in (r.get("results") or {}).get("entities", [])}
        if "GraphRAGClient" not in ent_names:
            fail(f"entidade do grafo ausente no fallback: {sorted(ent_names)}")
        r2 = router.query("GraphRAGClient", mode="okf")
        if r2["source"] != "NONE":
            fail(f"mode okf nao ignorou o grafo: source={r2['source']!r}")
        # Sem grafo: corpo OKF casa por busca ampla (OKF_BROAD_MATCH), sem crash.
        router3 = _router(okf_dir=base / "okf", base=base, graphrag_dir=None)
        r3 = router3.query("Authorization Bearer")
        if r3["source"] != "OKF_BROAD_MATCH":
            fail(f"busca ampla OKF: source={r3['source']!r}")
        r4 = router3.query("zzz nada casa")
        if r4["source"] != "NONE" or r4["found"] is not False:
            fail(f"sem match: source={r4['source']!r} found={r4['found']!r}")
        print("PASS: fallback graphrag, mode okf, busca ampla e NONE sem crash")
    finally:
        td.cleanup()


# --------------------------------------------------------------------------- #
# F. Limites (fail-closed / retry / contrato)
# --------------------------------------------------------------------------- #
def case_limite_fail_closed():
    """Pergunta: sem modo, sem store ou sem CSV o client falha de forma fechada?"""
    from hermes.platform.memory.graphrag import GraphRAGClient, GraphRAGError

    try:
        GraphRAGClient()
    except GraphRAGError:
        pass
    else:
        fail("GraphRAGClient() sem modo nao levantou GraphRAGError")

    td, base = _store_dir()
    try:
        c1 = GraphRAGClient(store_path=str(base / "missing.db"))
        if c1.available():
            fail("store ausente reportado como disponivel")
        try:
            c1.query_local("kafka")
        except GraphRAGError:
            pass
        else:
            fail("query com store ausente nao levantou GraphRAGError")

        empty = base / "empty-index"
        empty.mkdir()
        c2 = GraphRAGClient(index_dir=str(empty))
        if c2.available():
            fail("indice sem entities.csv reportado como disponivel")
        try:
            c2.query_global("kafka")
        except GraphRAGError:
            pass
        else:
            fail("query com indice vazio nao levantou GraphRAGError")
    finally:
        td.cleanup()
    print("PASS: fail-closed em modo, store e indice")
    return None


def case_limite_http_erro_tipado():
    """Pergunta: erro HTTP do servico vira erro TIPADO (boundary do retry)?"""
    from hermes.platform.memory import graphrag as G
    from hermes.platform.memory.graphrag import (
        GraphRAGClient,
        GraphRAGError,
        GraphRAGRemoteError,
    )

    def t500(method, url, body, headers):
        if url.endswith("/health"):
            return 200, b"{}"
        return 500, b"boom"

    c = GraphRAGClient(service_url="http://svc", transport=t500)
    try:
        c.query_global("q")
    except GraphRAGRemoteError as exc:
        if exc.status != 500:
            fail(f"status={exc.status} != 500")
    else:
        fail("POST /query 500 nao levantou GraphRAGRemoteError")

    # Boundary de transporte: URLError vira GraphRAGError (nao silencioso).
    with mock.patch.object(G.urllib.request, "urlopen",
                          side_effect=URLError("dns fail")):
        try:
            G._default_transport("GET", "http://svc/health", None, {})
        except GraphRAGError:
            pass
        else:
            fail("URLError nao virou GraphRAGError no transporte")

    # HTTPError vira (status, body) para o _query tratar.
    import io

    class _FakeResp:
        def read(self):
            return b"err-body"

    with mock.patch.object(
        G.urllib.request, "urlopen",
        side_effect=G.urllib.error.HTTPError("http://svc", 503, "svc", {}, _FakeResp()),
    ):
        status, body = G._default_transport("POST", "http://svc/query", b"{}", {})
        if (status, body) != (503, b"err-body"):
            fail(f"HTTPError passthrough: {(status, body)!r}")
    print("PASS: RemoteError(500), URLError->GraphRAGError, HTTPError passthrough")
    return None


def case_limite_reconciliador():
    """Pergunta: o reconciliador classifica ADD/NOOP/UPDATE/SUPERSEDE e esconde supersedidas?"""
    td, base = _store_dir()
    try:
        from hermes.platform.memory.reconciler import MemoryReconciler

        r = MemoryReconciler(base / "rec.db")
        a1 = r.reconcile(topic="deploy pipeline",
                         content="We deploy with GitHub Actions.", category="devops")
        a2 = r.reconcile(topic="deploy pipeline",
                         content="We deploy with GitHub Actions.")
        a3 = r.reconcile(topic="deploy pipeline",
                         content="We deploy with GitHub Actions and auto retry on failure.")
        a4 = r.reconcile(topic="deploy pipeline",
                         content="nao usamos mais GitHub Actions, agora usamos ArgoCD.")
        if (a1.action, a2.action, a3.action, a4.action) != \
           ("ADD", "NOOP", "UPDATE", "SUPERSEDE"):
            fail(f"acoes: {(a1.action, a2.action, a3.action, a4.action)}")
        # Topic normalizado em minusculas (contrato de chave): mesmo conteudo,
        # caixa diferente -> NOOP na MESMA chave (normalizacao quebrada viraria ADD).
        if r.reconcile(topic="Deploy Pipeline",
                       content="nao usamos mais GitHub Actions, "
                               "agora usamos ArgoCD.").action != "NOOP":
            fail("topic 'Deploy Pipeline' != 'deploy pipeline' (normalizacao quebrada)")
        active = r.get_active_memories(topic="deploy pipeline")
        if len(active) != 1 or "ArgoCD" not in active[0].content:
            fail(f"ativo inesperado: {[m.content for m in active]}")
        if r.get_active_memories(limit=0):
            fail("limit=0 nao respeitado")
        print("PASS: ADD/NOOP/UPDATE/SUPERSEDE, normalizacao e filtro ativo")
    finally:
        td.cleanup()


def case_limite_vault_sync():
    """Pergunta: nota nova no vault vira grafo + deepdoc NA HORA (sync imediato)?"""
    td, base = _store_dir()
    try:
        import os
        from hermes.platform.memory.haos_memory_sync import sync_note
        from hermes.platform.memory.graphrag import GraphRAGClient
        from hermes.platform.memory.ragflow_engine import RAGFlowStore

        note = base / "vault" / "ADR-042.md"
        note.parent.mkdir(parents=True)
        content = "# ADR-042\nAuditGateway connects to VaultReader."
        note.write_text(content, encoding="utf-8")
        res = sync_note(base, note, "ADR-042.md", "ADR-042 Audit", content)
        if res.get("deepdoc_chunks", 0) < 1 or res.get("entities", 0) < 1:
            fail(f"sync incompleto: {res}")
        client = GraphRAGClient(store_path=str(base / "memory" / "graphrag.db"))
        out = client.query_local("audit gateway")
        names = {e["entity"] for e in out["entities"]}
        if "AuditGateway" not in names:
            fail(f"grafo nao refletiu a nota nova: {sorted(names)}")
        chunks = RAGFlowStore(base / "memory" / "ragflow.db").hybrid_search(
            "AuditGateway", limit=3)
        if not chunks or "ADR-042.md" not in chunks[0].doc_path:
            fail(f"deepdoc nao refletiu a nota nova: {[c.doc_path for c in chunks]}")
        # A ferramenta graphrag_query (toolset memory) le o MESMO store canonico.
        os.environ["HERMES_HOME"] = str(base)
        from tools.haos_memory_tools import graphrag_query
        import json

        payload = json.loads(graphrag_query("audit gateway", mode="local"))
        if payload.get("success") is not True:
            fail(f"graphrag_query tool: {payload}")
        tool_names = {e["entity"] for e in payload["results"]["entities"]}
        if "AuditGateway" not in tool_names:
            fail(f"tool graphrag_query: {sorted(tool_names)}")
        print("PASS: vault -> grafo + deepdoc imediatos; tool le o store canonico")
    finally:
        td.cleanup()


_CASES = {
    "graphrag-extrai-relacoes-linha": case_graphrag_extrai_relacoes_linha,
    "graphrag-extrai-entidades": case_graphrag_extrai_entidades,
    "graphrag-extrai-idempotente": case_graphrag_extrai_idempotente,
    "graphrag-extrai-supersessao": case_graphrag_extrai_supersessao,
    "graphrag-extrai-modificado-poda": case_graphrag_extrai_modificado_poda,
    "graphrag-recall-semantico": case_graphrag_recall_semantico,
    "graphrag-recall-fallback-termos-curtos": case_graphrag_recall_fallback_termos_curtos,
    "graphrag-recall-um-salto": case_graphrag_recall_um_salto,
    "graphrag-multihop-dois-saltos": case_graphrag_multihop_dois_saltos,
    "graphrag-multihop-alvo-de-aresta": case_graphrag_multihop_alvo_de_aresta,
    "graphrag-multihop-comunidade-drift": case_graphrag_multihop_comunidade_drift,
    "ragflow-rrf-contrato": case_ragflow_rrf_contrato,
    "ragflow-rrf-pesos": case_ragflow_rrf_pesos,
    "ragflow-hybrid-frase-exata": case_ragflow_hybrid_frase_exata,
    "hibrido-okf-deterministico": case_hibrido_okf_deterministico,
    "hibrido-memoria-reconciliada": case_hibrido_memoria_reconciliada,
    "hibrido-fallback-graphrag": case_hibrido_fallback_graphrag,
    "limite-fail-closed": case_limite_fail_closed,
    "limite-http-erro-tipado": case_limite_http_erro_tipado,
    "limite-reconciliador": case_limite_reconciliador,
    "limite-vault-sync": case_limite_vault_sync,
}


def run_case(case_id: str) -> None:
    fn = _CASES.get(case_id)
    if fn is None:
        print(f"FAIL: caso desconhecido: {case_id!r}", file=sys.stderr)
        sys.exit(1)
    try:
        fn()
    except CaseFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        import traceback

        print(f"FAIL: excecao {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":  # depuracao direta: python -m evals.graphrag_lite.impl <id>
    run_case(sys.argv[1] if len(sys.argv) > 1 else "")
