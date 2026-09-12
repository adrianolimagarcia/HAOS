"""Contract tests for the graphrag-lite skill (probe script + park auditor).

Foco em COMPORTAMENTO (a prova de saude roda e reporta o que mede) e no gate do
auditor do parque com a skill incluida. A auditoria mecanica de frontmatter e do
auditor/authoring-standards, nao deste arquivo.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "skills" / "software-development" / "graphrag-lite"
PROBE = SKILL_DIR / "scripts" / "recall_probe.py"


def _run_probe(*args: str, hermes_home: Path) -> "subprocess.CompletedProcess[str]":
    env = dict(os.environ)
    env["HERMES_HOME"] = str(hermes_home)
    return subprocess.run(
        [sys.executable, str(PROBE), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=120,
    )


def test_skill_has_probe_script() -> None:
    """A skill entrega a maquinaria que ensina: o probe e parte do pacote."""
    assert PROBE.is_file(), "recall_probe.py ausente — a skill referencia este script"


def test_probe_self_contained_passes(tmp_path: Path) -> None:
    """Prova de saude: extracao -> persistencia -> consulta funcionam (sem rede)."""
    r = _run_probe("--self-contained", hermes_home=tmp_path)
    assert r.returncode == 0, f"probe falhou:\n{r.stdout}\n{r.stderr}"
    assert "PASS 6 checks" in r.stdout


def test_probe_read_only_fails_closed_on_empty_home(tmp_path: Path) -> None:
    """Home sem store canonico: probe falha de forma fechada, sem crash."""
    r = _run_probe(hermes_home=tmp_path)
    assert r.returncode != 0
    assert "store canonico ausente" in r.stderr
    assert "Traceback" not in r.stderr


def test_probe_read_only_reports_real_graph(tmp_path: Path) -> None:
    """Home com store real: probe le o grafo e reporta contagens."""
    from hermes.platform.context.memory.graphrag import GraphRAGAdapter
    from hermes.platform.context.memory.graphrag_store import GraphRAGStore
    from hermes.platform.context.memory.incremental_graphrag import (
        IncrementalGraphRAGUpdater,
    )
    from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventType

    home = tmp_path / "home"
    db = home / "memory" / "graphrag.db"
    store = GraphRAGStore(db)
    try:
        graph = GraphRAGAdapter()
        updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph, store=store)
        updater.process_event(KnowledgeEvent.create(
            event_type=KnowledgeEventType.NOTE_CREATED,
            uri="obsidian://20-Architecture/ADR-018.md",
            title="ADR-018 Protocol Fabric",
            content="Uses Kafka as messaging backbone.\n"
                    "ProtocolAdapter depends on ModelResolver.",
        ))
    finally:
        store.close()

    r = _run_probe(hermes_home=home)
    assert r.returncode == 0, f"probe read-only falhou:\n{r.stdout}\n{r.stderr}"
    assert "PASS: store=" in r.stdout


def test_skill_passes_park_auditor() -> None:
    """Gate do parque (P3): auditor deterministico com a skill nova incluida."""
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "audit_skills.py")],
        capture_output=True, text=True, cwd=str(REPO), timeout=300,
    )
    assert r.returncode == 0, f"auditor falhou:\n{r.stdout}\n{r.stderr}"
    assert "PASS" in r.stdout
    assert "0 violation(s)" in r.stdout
    # A skill nova e vista pelo iterador do loader como 1 skill, sem violacao.
    single = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "audit_skills.py"),
         str(SKILL_DIR)],
        capture_output=True, text=True, cwd=str(REPO), timeout=60,
    )
    assert single.returncode == 0, f"auditor (skill sozinha) falhou:\n{single.stdout}\n{single.stderr}"
    assert "1 skill(s)" in single.stdout
