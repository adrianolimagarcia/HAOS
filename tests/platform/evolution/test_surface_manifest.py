"""Contrato do manifesto de superfícies (``hermes/platform/evolution/surface_manifest.py``).

Invariante: a régua (``evals/``), o juiz (o gate de evolução, o motor procedural), o
registro de evidência e a configuração de modelo são read-only para um otimizador, e o
produto dele (skills do agente, memórias, jobs, logs) é gravável. Superfície não
declarada não é gravável — negar é o padrão.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.platform.evolution.surface_manifest import (
    EDITABLE_SURFACES,
    READ_ONLY_SURFACES,
    ReadOnlySurfaceError,
    assert_writable,
    classify,
    is_writable,
    render_manifest,
)

REPO = Path(__file__).resolve().parents[3]


def test_manifest_decides_writability_by_declared_surface(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Read-only: a régua, o juiz que decide, o registro do que aconteceu e o
    # modelo que julga. Um otimizador que escreve aqui não precisa melhorar nada.
    read_only = [
        REPO / "evals" / "holdout_split.py",
        REPO / "hermes" / "platform" / "evals" / "runner.py",
        REPO / "hermes" / "platform" / "evolution" / "surface_manifest.py",
        REPO / "hermes" / "platform" / "evolution" / "ouroboros_lifecycle.py",
        REPO / "hermes" / "platform" / "evolution" / "ledger.py",
        REPO / "hermes" / "platform" / "observability" / "event_store.py",
        REPO / "hermes" / "platform" / "skills" / "procedural_engine.py",
        REPO / "agent" / "curator.py",
        REPO / "agent" / "verification_evidence.py",
        REPO / "tools" / "skill_usage.py",
        REPO / "tests" / "conftest.py",
        home / "state.db",
        home / "config.yaml",
        home / "verification_evidence.db",
        home / ".env",
    ]
    for path in read_only:
        decision = classify(path)
        assert decision.writable is False, path
        assert decision.reason, path  # toda recusa vem com motivo para o log do consumidor
        assert is_writable(path) is False, path
        with pytest.raises(ReadOnlySurfaceError):
            assert_writable(path)
    assert "state.db" in classify(home / "state.db").reason

    # Editável: o produto do otimizador agendado segue gravável (sem regressão para
    # o fluxo do curador — consolidar, arquivar, reportar, re-apontar cron).
    writable = [
        home / "skills" / "minha-skill" / "SKILL.md",
        home / "skills" / ".archive" / "antiga" / "SKILL.md",
        home / "memories" / "fato.md",
        home / "logs" / "curator" / "run.json",
        home / "cron" / "jobs.json",
    ]
    for path in writable:
        assert is_writable(path) is True, path
        assert assert_writable(path) == path.resolve()

    # Nega por padrão: fora das superfícies declaradas o otimizador não escreve — e o
    # motivo diz que basta declarar, se a escrita for mesmo produto dele.
    for path in (home / "algo-inesperado.txt", tmp_path / "fora" / "x.json"):
        assert is_writable(path) is False, path
        assert "não declarada" in classify(path).reason

    # O manifesto é inspecionável: o texto renderizado cobre todas as superfícies
    # declaradas (é o que um relatório/ledger de decisão cita).
    rendered = render_manifest()
    for surface in EDITABLE_SURFACES + READ_ONLY_SURFACES:
        assert surface.label() in rendered
