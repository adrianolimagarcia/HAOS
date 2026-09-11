#!/usr/bin/env python3
"""Popula a memória canônica do HAOS a partir do vault Obsidian (idempotente).

Três passos, cada um independente (uma falha não impede os outros):

  1. DeepDoc/RAG — indexa as notas do vault em ``<home>/memory/ragflow.db``
     (FTS5 + breadcrumbs de header), o store que o ``haos-edge doc search`` lê.
  2. GraphRAG    — reconstrói o store canônico ``<home>/memory/graphrag.db``
     (ADR-008) a partir das notas, pela mesma cadeia que o dashboard usa
     (ObsidianAdapter → GraphRAGAdapter → IncrementalGraphRAGUpdater).
  3. Dream       — consolida sessões em memórias reconciliadas + lições OKF.

O home é resolvido por ``HERMES_HOME``/``HAOS_HOME`` e, na ausência dos dois,
cai em ``~/.haos`` com aviso. Isso é deliberado: ``get_hermes_home()`` sem env
resolve para ``~/.hermes`` e o appliance passa a escrever num store órfão — o
split-brain que fez ``hermes haos doc index`` gravar chunks fora do store que os
daemons leem. O script fixa o env resolvido antes de importar o runtime, para
que TODOS os caminhos concordem.

Uso:
    haos_memory_populate.py [--home DIR] [--skip-dream] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def resolve_home(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    for var in ("HERMES_HOME", "HAOS_HOME"):
        value = os.environ.get(var, "").strip()
        if value:
            return Path(value).expanduser().resolve()
    fallback = Path.home() / ".haos"
    print(
        f"[aviso] HERMES_HOME/HAOS_HOME não definidos; usando {fallback} "
        "(sem isso o runtime cairia em ~/.hermes)",
        file=sys.stderr,
    )
    return fallback


def index_vault_rag(home: Path) -> dict:
    """Passo 1: vault -> memory/ragflow.db (DeepDoc/RAG, FTS5)."""
    from hermes.platform.memory.ragflow_engine import RAGFlowStore

    vault = home / "obsidian_vault"
    if not vault.is_dir():
        return {"status": "skipped", "reason": f"vault ausente: {vault}"}

    store = RAGFlowStore(db_path=home / "memory" / "ragflow.db")
    indexed = store.index_directory(vault, glob_pattern="**/*.md")
    return {
        "status": "ok",
        "db": str(store.db_path),
        "files": len(indexed),
        "chunks": sum(indexed.values()),
    }


def build_graphrag_store(home: Path) -> dict:
    """Passo 2: vault -> memory/graphrag.db (store canônico, ADR-008)."""
    from hermes.platform.context.memory.events import (
        KnowledgeEvent,
        KnowledgeEventType,
    )
    from hermes.platform.context.memory.graphrag import GraphRAGAdapter
    from hermes.platform.context.memory.graphrag_store import GraphRAGStore
    from hermes.platform.context.memory.incremental_graphrag import (
        IncrementalGraphRAGUpdater,
    )
    from hermes.platform.context.memory.obsidian import ObsidianAdapter

    vault = home / "obsidian_vault"
    if not vault.is_dir():
        return {"status": "skipped", "reason": f"vault ausente: {vault}"}

    # ObsidianAdapter(ContextSource) não expõe available(): retrieve() devolve []
    # quando o vault não existe, então a checagem de vault vazio vem do count.
    # As notas são coletadas antes de abrir o store para não criar um store vazio.
    notes = list(ObsidianAdapter(vault).retrieve())
    if not notes:
        return {"status": "skipped", "reason": f"vault sem notas .md: {vault}"}

    # GraphRAGAdapter é só memória; a persistência no SQLite canônico (ADR-008) é
    # o write-through `store=` do updater. Sem ele o store nunca é escrito — foi
    # por isso que graphrag.db não existia no nó.
    store_path = home / "memory" / "graphrag.db"
    graph = GraphRAGAdapter()
    with GraphRAGStore(db_path=store_path) as store:
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        for note in notes:
            updater.process_event(
                KnowledgeEvent.create(
                    event_type=KnowledgeEventType.NOTE_CREATED,
                    uri=note.source_uri,
                    title=note.title,
                    content=note.content,
                )
            )
        counts = store.counts()
    return {"status": "ok", "store": str(store_path), "notes": len(notes), **counts}


def run_dream(home: Path) -> dict:
    """Passo 3: consolidação de sessões (memórias reconciliadas + OKF)."""
    from hermes.platform.memory.dream import DreamConsolidator

    consolidator = DreamConsolidator(hermes_home=home)
    result = consolidator.run_dream(dry_run=False)
    return {
        "status": result.get("status"),
        "consolidated": result.get("consolidated_count"),
        "commit": result.get("commit"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Popula a memória canônica do HAOS")
    parser.add_argument("--home", default=None, help="HAOS_HOME (default: env ou ~/.haos)")
    parser.add_argument("--skip-dream", action="store_true", help="não consolidar sessões")
    parser.add_argument("--json", action="store_true", help="saída JSON (para cron/log)")
    args = parser.parse_args()

    home = resolve_home(args.home)
    # Fixa o env resolvido ANTES de importar o runtime: get_hermes_home() passa a
    # concordar com o home deste script em todos os caminhos.
    os.environ["HERMES_HOME"] = str(home)
    os.environ.setdefault("HAOS_HOME", str(home))

    if not home.is_dir():
        print(f"[erro] home inexistente: {home}", file=sys.stderr)
        return 2

    steps: dict[str, dict] = {}
    failures = 0
    plan = [
        ("deepdoc_rag", lambda: index_vault_rag(home)),
        ("graphrag", lambda: build_graphrag_store(home)),
    ]
    if not args.skip_dream:
        plan.append(("dream", lambda: run_dream(home)))

    for name, fn in plan:
        try:
            steps[name] = fn()
        except Exception as exc:  # cada passo reporta a própria falha
            failures += 1
            steps[name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    report = {"home": str(home), "steps": steps, "failures": failures}
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"HAOS memória canônica — home: {home}")
        for name, res in steps.items():
            detail = ", ".join(f"{k}={v}" for k, v in res.items() if k != "status")
            print(f"  • {name}: {res.get('status')} ({detail})")
        print(f"  falhas: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
