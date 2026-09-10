"""Wiring do event store de produção (P1 do plano de auto-melhoria).

Contrato: os comandos do CLI HAOS consomem o MESMO stream de eventos que os
produtores gravam no perfil ativo. Antes, cada comando construía ``EventStore()``
— que abre ``:memory:`` — então lia um banco novo e vazio em cada processo:
``haos evolution status`` respondia "0 propostas" mesmo com propostas reais
persistidas no disco.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hermes.platform.evolution.ledger import EvolutionLedger
from hermes.platform.observability.event_store import (
    EventStore,
    default_event_store_path,
    get_event_store,
)
from hermes_cli.haos_cmd import cmd_haos_evolution_status


def test_event_store_path_is_profile_scoped(monkeypatch, tmp_path):
    """O caminho canônico acompanha o perfil ativo, nunca um literal fixo."""
    home = tmp_path / ".haos"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    assert default_event_store_path() == home / "events.db"
    assert get_event_store().db_path == str(home / "events.db")


def test_event_store_instances_share_the_persisted_stream(monkeypatch, tmp_path):
    """Uma proposta gravada por um processo é visível para outro (mesmo arquivo)."""
    home = tmp_path / ".haos"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    proposal = {
        "proposal_id": "p-wiring-1",
        "target": "skills",
        "current_profile": "baseline",
        "proposed_profile": "candidate",
        "rationale": "regressão medida no probe X",
    }
    EvolutionLedger(get_event_store()).submit(proposal)

    # Instância NOVA (equivale a outro processo) tem de enxergar a proposta.
    pending = EvolutionLedger(get_event_store()).pending()
    assert [p["proposal_id"] for p in pending] == ["p-wiring-1"]


def test_evolution_status_reports_persisted_proposals(monkeypatch, tmp_path, capsys):
    """``haos evolution status`` lê o stream real, não um store em memória.

    Regressão exata do bug: com o store em memória o comando imprimia
    "Propostas Pendentes : 0" e "Nenhuma proposta pendente no momento".
    """
    home = tmp_path / ".haos"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    EvolutionLedger(get_event_store()).submit(
        {
            "proposal_id": "p-status-1",
            "target": "retrieval_top_k",
            "current_profile": "k=5",
            "proposed_profile": "k=8",
            "rationale": "recall melhorou no golden set",
        }
    )

    rc = cmd_haos_evolution_status(argparse.Namespace(json=False))
    out = capsys.readouterr().out

    assert rc == 0
    assert "Propostas Pendentes : 1" in out
    assert "p-status-1" in out
    assert "Nenhuma proposta pendente" not in out


def test_memory_store_stays_isolated(tmp_path):
    """Quem pede isolamento continua isolado: ``EventStore()`` não toca o disco.

    Garante que a correção não transformou o default (usado por testes e por
    consumidores efêmeros) em escrita no perfil do usuário.
    """
    store = EventStore()
    assert store.db_path == ":memory:"
    assert not list(Path(tmp_path).glob("events.db"))
