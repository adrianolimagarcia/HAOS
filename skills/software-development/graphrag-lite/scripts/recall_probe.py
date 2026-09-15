#!/usr/bin/env python3
"""recall_probe.py — prova deterministica do toolchain GraphRAG do HAOS.

Dois modos:

  --self-contained : monta um grafo fixture em tempdir (KnowledgeEvent ->
                     IncrementalGraphRAGUpdater -> GraphRAGStore -> GraphRAGClient),
                     roda os contratos de extracao/persistencia/consulta e sai
                     0 quando tudo passa, 1 na primeira violacao. Nao toca o
                     home real (usa tempfile) e nao usa rede.

  (default)        : probe READ-ONLY do store canonico $HERMES_HOME/memory/graphrag.db
                     via GraphRAGClient (sqlite mode). Reporta contagens e o
                     resultado de uma consulta fixa. Exit 0 = store presente e
                     consultavel; exit 1 com mensagem clara = fail-closed.

Uso (a partir da raiz do repo, venv ativo):

  python3 skills/software-development/graphrag-lite/scripts/recall_probe.py --self-contained
  python3 skills/software-development/graphrag-lite/scripts/recall_probe.py [--home DIR]

Esta skill nao cria ferramenta nova nem altera o grafo: so exercita a
maquinaria existente (GOV-008) e reporta o que mediu.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 'backbone' aparece cedo (a descricao da entidade primaria trunca em 150
# chars — contrato real do updater — entao o termo discriminante tem de
# caber no inicio do corpo).
_ADR = (
    "# ADR-018: Protocol Fabric\n"
    "Uses Kafka as messaging backbone.\n"
    "ProtocolAdapter depends on ModelResolver.\n"
    "ProtocolAdapter connects to Dispatcher.\n"
    "See [[ANP-Spec]] for details.\n"
)

_CHECKS: list[tuple[str, "Callable[[], str]"]] = []


def _check(name: str):
    def deco(fn):
        _CHECKS.append((name, fn))
        return fn

    return deco


@_check("extracao de relacoes de linha (depends on / connects to)")
def _extract_relations():
    from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventType
    from hermes.platform.context.memory.graphrag import GraphRAGAdapter
    from hermes.platform.context.memory.graphrag_store import GraphRAGStore
    from hermes.platform.context.memory.incremental_graphrag import IncrementalGraphRAGUpdater

    with tempfile.TemporaryDirectory() as td:
        store = GraphRAGStore(Path(td) / "g.db")
        graph = GraphRAGAdapter()
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            updater.process_event(KnowledgeEvent.create(
                event_type=KnowledgeEventType.NOTE_CREATED,
                uri="obsidian://20-Architecture/ADR-018.md",
                title="ADR-018 Protocol Fabric",
                content=_ADR,
            ))
            rels = {(r["source"], r["relation_type"], r["target"]) for r in store.list_relations()}
            want = {
                ("ProtocolAdapter", "depends_on", "ModelResolver"),
                ("ProtocolAdapter", "connects_to", "Dispatcher"),
                ("ADR-018 Protocol Fabric", "mentions_component", "ProtocolAdapter"),
                ("ADR-018 Protocol Fabric", "uses_technology", "Kafka"),
            }
            if not want <= rels:
                return f"relacoes faltando: {sorted(want - rels)} (presentes={sorted(rels)})"
        finally:
            store.close()
    return "ok"


@_check("extrai entidades (componente, tecnologia, wikilink, ADR)")
def _extract_entities():
    from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventType
    from hermes.platform.context.memory.graphrag import GraphRAGAdapter
    from hermes.platform.context.memory.graphrag_store import GraphRAGStore
    from hermes.platform.context.memory.incremental_graphrag import IncrementalGraphRAGUpdater

    with tempfile.TemporaryDirectory() as td:
        store = GraphRAGStore(Path(td) / "g.db")
        graph = GraphRAGAdapter()
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            updater.process_event(KnowledgeEvent.create(
                event_type=KnowledgeEventType.NOTE_CREATED,
                uri="obsidian://20-Architecture/ADR-018.md",
                title="ADR-018 Protocol Fabric",
                content=_ADR,
            ))
            kinds = {e["entity"]: e["entity_type"] for e in store.list_entities()}
            want = {
                "ProtocolAdapter": "Component",
                "Kafka": "Technology",
                "ANP-Spec": "Concept",
                "ADR-018": "ADR",
            }
            bad = {k: (kinds.get(k), v) for k, v in want.items() if kinds.get(k) != v}
            if bad:
                return f"tipos errados: {bad}"
        finally:
            store.close()
    return "ok"


@_check("reprocessar o mesmo evento nao duplica (upsert por PK)")
def _idempotent_reprocess():
    from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventType
    from hermes.platform.context.memory.graphrag import GraphRAGAdapter
    from hermes.platform.context.memory.graphrag_store import GraphRAGStore
    from hermes.platform.context.memory.incremental_graphrag import IncrementalGraphRAGUpdater

    with tempfile.TemporaryDirectory() as td:
        store = GraphRAGStore(Path(td) / "g.db")
        graph = GraphRAGAdapter()
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            ev = KnowledgeEvent.create(
                event_type=KnowledgeEventType.NOTE_CREATED,
                uri="obsidian://20-Architecture/ADR-018.md",
                title="ADR-018 Protocol Fabric",
                content=_ADR,
            )
            updater.process_event(ev)
            first = store.counts()
            updater.process_event(ev)
            second = store.counts()
            if first != second:
                return f"contagens mudaram no reprocessamento: {first} -> {second}"
        finally:
            store.close()
    return "ok"


@_check("consulta sqlite: termo so na descricao alcanca a entidade (1 salto)")
def _client_semantic_recall():
    from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventType
    from hermes.platform.context.memory.graphrag import GraphRAGAdapter
    from hermes.platform.context.memory.graphrag_store import GraphRAGStore
    from hermes.platform.context.memory.incremental_graphrag import IncrementalGraphRAGUpdater
    from hermes.platform.memory.graphrag import GraphRAGClient

    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "g.db")
        store = GraphRAGStore(db)
        graph = GraphRAGAdapter()
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            updater.process_event(KnowledgeEvent.create(
                event_type=KnowledgeEventType.NOTE_CREATED,
                uri="obsidian://20-Architecture/ADR-018.md",
                title="ADR-018 Protocol Fabric",
                content=_ADR,
            ))
        finally:
            store.close()

        client = GraphRAGClient(store_path=db)
        out = client.query_local("qual o backbone de mensageria?")  # 'backbone' so na descricao
        names = {e["entity"] for e in out["entities"]}
        if "ADR-018 Protocol Fabric" not in names:
            return f"backbone (descricao) nao alcancou a nota: {sorted(names)}"
        rels = out["relationships"]
        if not any(r["source"] == "ProtocolAdapter" or r["target"] == "ProtocolAdapter" for r in rels):
            return "nenhuma relacao tocando ProtocolAdapter no resultado"
    return "ok"


@_check("NOTE_DELETED remove so o que a URI alegava em exclusivo")
def _deletion_scoped():
    from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventType
    from hermes.platform.context.memory.graphrag import GraphRAGAdapter
    from hermes.platform.context.memory.graphrag_store import GraphRAGStore
    from hermes.platform.context.memory.incremental_graphrag import IncrementalGraphRAGUpdater

    with tempfile.TemporaryDirectory() as td:
        store = GraphRAGStore(Path(td) / "g.db")
        graph = GraphRAGAdapter()
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        try:
            updater.process_event(KnowledgeEvent.create(
                event_type=KnowledgeEventType.NOTE_CREATED,
                uri="obsidian://20-Architecture/ADR-100.md",
                title="ADR-100 Legacy",
                content="LegacyAuthAdapter depends on OldSessionStore.",
            ))
            updater.process_event(KnowledgeEvent.create(
                event_type=KnowledgeEventType.NOTE_CREATED,
                uri="obsidian://20-Architecture/ADR-200.md",
                title="ADR-200 Modern",
                content="ModernAuthAdapter depends on NewSessionStore.",
            ))
            updater.process_event(KnowledgeEvent.create(
                event_type=KnowledgeEventType.NOTE_DELETED,
                uri="obsidian://20-Architecture/ADR-100.md",
                title="",
                content="",
            ))
            names = {e["entity"] for e in store.list_entities()}
            if "LegacyAuthAdapter" in names or "OldSessionStore" in names:
                return "entidades do ADR-100 nao removidas na delecao"
            if "ModernAuthAdapter" not in names or "NewSessionStore" not in names:
                return "delecao do ADR-100 removeu entidades do ADR-200"
        finally:
            store.close()
    return "ok"


@_check("fail-closed: sem modo configurado o client levanta GraphRAGError")
def _fail_closed_no_mode():
    from hermes.platform.memory.graphrag import GraphRAGClient, GraphRAGError

    try:
        GraphRAGClient()
    except GraphRAGError:
        return "ok"
    return "GraphRAGClient() sem modo nao levantou GraphRAGError"


def run_self_contained() -> int:
    failures = []
    for name, fn in _CHECKS:
        try:
            detail = fn()
        except Exception as exc:  # noqa: BLE001
            detail = f"excecao {type(exc).__name__}: {exc}"
        status = "PASS" if detail == "ok" else "FAIL"
        print(f"[{status}] {name}" + ("" if detail == "ok" else f" :: {detail}"))
        if status == "FAIL":
            failures.append(name)
    n_pass = len(_CHECKS) - len(failures)
    print(f"PASS {n_pass} checks" if not failures else f"FAIL {len(failures)} check(s)")
    return 1 if failures else 0


def run_read_only(home: Path) -> int:
    from hermes.platform.memory.graphrag import GraphRAGClient

    db = home / "memory" / "graphrag.db"
    if not db.is_file():
        print(f"FAIL: store canonico ausente: {db} (fail-closed)", file=sys.stderr)
        return 1
    client = GraphRAGClient(store_path=str(db))
    if not client.available():
        print(f"FAIL: store presente mas unavailable: {db}", file=sys.stderr)
        return 1
    out = client.query_local("graphrag")
    n_ent = len(out.get("entities") or [])
    n_rel = len(out.get("relationships") or [])
    print(f"PASS: store={db} mode={out.get('mode')} entidades_matches={n_ent} relacoes_matches={n_rel}")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-contained", action="store_true",
                    help="prova deterministica em tempdir (nao toca o home)")
    ap.add_argument("--home", default=None,
                    help="home a sondar no modo read-only (default: HERMES_HOME)")
    args = ap.parse_args(argv)

    if args.self_contained:
        return run_self_contained()

    home = Path(args.home) if args.home else Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return run_read_only(home)


if __name__ == "__main__":
    sys.exit(main())
