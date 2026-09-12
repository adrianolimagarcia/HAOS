"""Testes de contrato do P9 — verificação antes de mais agentes.

Interpretação documentada (a spec só diz "verificação antes de mais agentes"):
gate de verificação ANTES de delegar/escalar — aditivo aos caps 3/2 (não
removidos): só pode RECUSAR um spawn que já passaria nos caps. Aqui:

  * ``verification_spawn_gate`` — decisão pura (fail-closed quando
    ``require`` ligado e o pai tem edições não verificadas; libera com
    atestação ou sem edições; knob desligado = comportamento atual intacto);
  * ``spawn_verification_message`` — wiring: lê a config + o estado real do
    pai (``_turn_file_mutation_paths``) e devolve a recusa, ou None;
  * ``delegate_task`` — o gate roda antes de GERAR qualquer agente.

Nenhum teste lê texto de código-fonte; nenhum é change-detector.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.delegate_verification_gate import (
    spawn_verification_message,
    verification_spawn_gate,
)


# ── Decisão pura ─────────────────────────────────────────────────────────────

def test_gate_off_sempre_libera():
    """Knob desligado: comportamento atual intacto — spawn sempre liberado."""
    allowed, msg = verification_spawn_gate(verified=None, unverified_edits=True, require=False)
    assert allowed is True and msg == ""
    allowed, _ = verification_spawn_gate(verified=None, unverified_edits=False, require=False)
    assert allowed is True


def test_gate_on_recusa_edicao_nao_verificada():
    """Fail-closed: com o knob ligado e edições no turno SEM atestação, recusa."""
    allowed, msg = verification_spawn_gate(verified=None, unverified_edits=True, require=True)
    assert allowed is False
    assert "verification" in msg and "unverified changes" in msg


def test_gate_on_atestacao_ou_sem_edicao_libera():
    """Com o knob ligado: atestação explícita OU ausência de edições libera."""
    allowed, msg = verification_spawn_gate(verified=True, unverified_edits=True, require=True)
    assert allowed is True and msg == ""
    allowed, msg = verification_spawn_gate(verified=None, unverified_edits=False, require=True)
    assert allowed is True and msg == ""


def test_gate_deterministico():
    args = dict(verified=None, unverified_edits=True, require=True)
    assert verification_spawn_gate(**args) == verification_spawn_gate(**dict(args))


# ── Wiring: lê o estado real do pai ──────────────────────────────────────────

def test_spawn_verification_message_refusa_com_edicoes_do_turno(monkeypatch):
    monkeypatch.setattr(
        "tools.delegate_verification_gate._get_require_verification_before_spawn",
        lambda: True,
    )
    parent = SimpleNamespace(_turn_file_mutation_paths={"src/x.py"})
    msg = spawn_verification_message(parent)
    assert msg is not None
    assert "unverified changes" in msg


def test_spawn_verification_message_libera_com_atestacao(monkeypatch):
    monkeypatch.setattr(
        "tools.delegate_verification_gate._get_require_verification_before_spawn",
        lambda: True,
    )
    parent = SimpleNamespace(_turn_file_mutation_paths={"src/x.py"}, _work_verified=True)
    assert spawn_verification_message(parent) is None


def test_spawn_verification_message_knob_off_ignora_estado(monkeypatch):
    monkeypatch.setattr(
        "tools.delegate_verification_gate._get_require_verification_before_spawn",
        lambda: False,
    )
    parent = SimpleNamespace(_turn_file_mutation_paths={"src/x.py"})
    assert spawn_verification_message(parent) is None


# ── E2E: delegate_task consulta o gate antes de gerar agentes ────────────────

def test_delegate_task_refusa_antes_de_gerar_quando_gate_dispara(monkeypatch):
    """Com o gate ligado e edições não verificadas no turno, delegate_task
    devolve a recusa SEM gerar nenhum agente (fail-closed no caminho real)."""
    monkeypatch.setattr(
        "tools.delegate_verification_gate._get_require_verification_before_spawn",
        lambda: True,
    )
    # Se o gate não disparar, _build_children seria chamado e explodiria —
    # a recusa precisa acontecer ANTES.
    def _boom(*args, **kwargs):
        raise AssertionError("delegate_task gerou agentes apesar do gate")

    monkeypatch.setattr("tools.delegate_tool._build_children", _boom)
    from tools.delegate_tool import delegate_task

    parent = SimpleNamespace(_turn_file_mutation_paths={"src/x.py"}, _delegate_depth=0)
    out = delegate_task(goal="do the thing", parent_agent=parent)
    assert "verification gate" in out
    assert "unverified changes" in out