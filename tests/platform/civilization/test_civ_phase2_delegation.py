"""Suite de testes para a Fase 2: Delegação Ativa -> Civilização.

Testa os contratos das 4 fases:
- Fase 2A: Ponto de entrada e observação (0 efeitos colaterais).
- Fase 2B: Delegação opt-in por flag/sessão e fallback resiliente.
- Fase 2C: Auto-criação controlada por flag e limite por sessão.
- Fase 2D: Roteamento ativo no gateway e rollback instantâneo.
"""

from __future__ import annotations

import os
from typing import Any, Dict
import pytest

from hermes.platform.civilization.delegation import (
    civ_delegation_enabled,
    is_task_delegatable,
    route_task_observe,
    apply_civ_bot_to_session,
    _auto_create_allowed,
    _record_session_bot_creation,
    _SESSION_AUTO_CREATE_COUNTS,
    _SESSION_CREATION_LOCK,
    route_task,
)
from hermes.platform.civilization import gateway_bridge as bridge
from hermes.platform.civilization.feature_flags import reset_feature_flags
from hermes.platform.observability.event_store import EventStore
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.identity import BotIdentityBundle, BotIdentitySpec


@pytest.fixture(autouse=True)
def _clean_test_env(monkeypatch):
    """Limpa variáveis de ambiente e caches de estado entre testes."""
    monkeypatch.delenv("HAOS_CIV_DELEGATE", raising=False)
    monkeypatch.delenv("HAOS_CIV_AUTO_CREATE", raising=False)
    monkeypatch.delenv("HAOS_CIV_GATEWAY_ROUTING", raising=False)
    monkeypatch.delenv("HAOS_CIV_ENABLED", raising=False)
    monkeypatch.delenv("HAOS_CIV_MAX_AUTO_CREATE_PER_SESSION", raising=False)
    with _SESSION_CREATION_LOCK:
        _SESSION_AUTO_CREATE_COUNTS.clear()
    reset_feature_flags()
    yield
    reset_feature_flags()
    with _SESSION_CREATION_LOCK:
        _SESSION_AUTO_CREATE_COUNTS.clear()


# ===========================================================================
# FASE 2A: Ponto de Entrada e Observação
# ===========================================================================

def test_phase_2a_observe_reverse_engineering_prompt():
    """Prompt com termos de engenharia reversa deve sugerir reverse-bot."""
    bot = route_task_observe("analise este binário ELF descompilando com ghidra para achar buffer overflow")
    assert bot == "reverse-bot"


def test_phase_2a_observe_research_prompt():
    """Prompt com termos de pesquisa deve sugerir research-bot."""
    bot = route_task_observe("faça uma pesquisa aprofundada na web e sintetize as fontes técnicas")
    assert bot == "research-bot"


def test_phase_2a_observe_generic_prompt():
    """Prompt de desenvolvimento geral sem especialista mapeado escolhe generic-bot."""
    bot = route_task_observe("crie uma função em Python para somar dois inteiros e validar tipagem")
    assert bot == "generic-bot"


def test_phase_2a_observe_non_delegatable_greetings():
    """Saudações e conversas casuais não são delegáveis e retornam None."""
    assert route_task_observe("olá, tudo bem?") is None
    assert route_task_observe("bom dia") is None
    assert route_task_observe("oi") is None


def test_phase_2a_observe_zero_side_effects():
    """Modo observação nunca deve criar bots nem alterar o event store."""
    store = EventStore()
    bot = route_task_observe(
        "decompile this firmware dump with radare2 and find vulnerabilities",
        event_store=store,
    )
    assert bot == "reverse-bot"
    assert len(store.get_all()) == 0, "Observação causou efeitos colaterais no event store!"


# ===========================================================================
# FASE 2B: Delegação Opt-in e Fallbacks
# ===========================================================================

def test_phase_2b_delegation_disabled_by_default():
    """Por padrão, delegação ativa deve estar desativada."""
    assert not civ_delegation_enabled()
    assert not civ_delegation_enabled({})


def test_phase_2b_delegation_enabled_via_env(monkeypatch):
    """Flag HAOS_CIV_DELEGATE=1 ativa a delegação."""
    monkeypatch.setenv("HAOS_CIV_DELEGATE", "1")
    assert civ_delegation_enabled()

    monkeypatch.setenv("HAOS_CIV_DELEGATE", "true")
    assert civ_delegation_enabled()


def test_phase_2b_delegation_enabled_per_session():
    """Sessão específica pode ativar civ_delegate mesmo com flag global OFF."""
    assert not civ_delegation_enabled()
    session = {"civ_delegate": True}
    assert civ_delegation_enabled(session)


def test_phase_2b_apply_civ_bot_to_session():
    """Aplica identidade do bot na sessão com sucesso."""
    store = EventStore()
    spec_mgr = BotSpecManager(store)
    id_mgr = IdentityManager(store)

    spec_mgr.create_bot("test-bot", purpose="test bot")
    bundle = BotIdentityBundle(bot_id="test-bot", identity="Você é um bot de teste.")
    id_mgr.create_version("test-bot", bundle=bundle, activate=True)

    class MockAgent:
        bot_id = None
        identity_bundle = None

    session = {"agent": MockAgent()}
    applied = apply_civ_bot_to_session(session, "test-bot", event_store=store)

    assert applied is True
    assert session.get("bot_id") == "test-bot"
    assert session["agent"].bot_id == "test-bot"
    assert session["agent"].identity_bundle == bundle


def test_phase_2b_fallback_graceful_on_error():
    """Se o bot não existir, apply_civ_bot_to_session retorna False sem quebrar a sessão."""
    store = EventStore()
    class MockAgent:
        bot_id = "initial"
    session = {"agent": MockAgent()}
    applied = apply_civ_bot_to_session(session, "bot-inexistente-12345", event_store=store)

    assert applied is False
    assert session.get("bot_id") is None
    # Agente não quebrou
    assert session["agent"].bot_id == "initial"


# ===========================================================================
# FASE 2C: Auto-criação Controlada
# ===========================================================================

def test_phase_2c_auto_create_disabled_by_default():
    """Por segurança, auto-criação deve ser default OFF."""
    assert not _auto_create_allowed()


def test_phase_2c_auto_create_enabled_via_flag(monkeypatch):
    """Com HAOS_CIV_AUTO_CREATE=1, auto-criação é permitida."""
    monkeypatch.setenv("HAOS_CIV_AUTO_CREATE", "1")
    assert _auto_create_allowed()


def test_phase_2c_session_limit_enforced(monkeypatch):
    """Respeita limite máximo de auto-criações por sessão (default 3)."""
    monkeypatch.setenv("HAOS_CIV_AUTO_CREATE", "1")
    session_id = "sess-abc-123"

    assert _auto_create_allowed(session_id)
    _record_session_bot_creation(session_id)
    assert _auto_create_allowed(session_id)
    _record_session_bot_creation(session_id)
    assert _auto_create_allowed(session_id)
    _record_session_bot_creation(session_id)

    # Atingiu 3 criações -> bloqueia
    assert not _auto_create_allowed(session_id)


def test_phase_2c_kill_switch_overrides_session(monkeypatch):
    """Kill-switch HAOS_CIV_ENABLED=0 bloqueia auto-criação mesmo se flag estiver ON."""
    monkeypatch.setenv("HAOS_CIV_AUTO_CREATE", "1")
    monkeypatch.setenv("HAOS_CIV_ENABLED", "0")
    reset_feature_flags()
    assert not _auto_create_allowed()


def test_phase_2c_auto_create_off_routes_to_generic(monkeypatch):
    """Quando auto-criação está OFF, task não coberta vai para generic-bot."""
    monkeypatch.setenv("HAOS_CIV_AUTO_CREATE", "0")
    store = EventStore()
    task = {
        "goal": "Construir um sistema novo de computação quântica topológica em Rust",
        "context": "quantum topological braiding simulation",
    }
    routed = route_task(task, event_store=store)
    # Como quantum não tem bot existente e auto-create está OFF, vai para generic-bot
    assert routed == "generic-bot"


# ===========================================================================
# FASE 2D: Integração no Gateway e Rollback Instantâneo
# ===========================================================================

def test_phase_2d_gateway_rollback_instantaneo(monkeypatch):
    """HAOS_CIV_GATEWAY_ROUTING=0 desativa roteamento imediatamente."""
    monkeypatch.setenv("HAOS_CIV_GATEWAY_ROUTING", "0")
    monkeypatch.setenv("HAOS_CIV_DELEGATE", "1")
    assert not bridge.gateway_active_routing_enabled()


def test_phase_2d_gateway_active_routing_requires_delegate_opt_in(monkeypatch):
    """Roteamento ativo no gateway exige opt-in explícito."""
    monkeypatch.delenv("HAOS_CIV_DELEGATE", raising=False)
    assert not bridge.gateway_active_routing_enabled()

    monkeypatch.setenv("HAOS_CIV_DELEGATE", "1")
    assert bridge.gateway_active_routing_enabled()


def test_phase_2d_route_turn_to_specialist():
    """route_turn_to_specialist injeta bot_id e bundle no AIAgent."""
    store = EventStore()
    spec_mgr = BotSpecManager(store)
    id_mgr = IdentityManager(store)

    spec_mgr.create_bot("reverse-bot", purpose="reverse engineering bot")
    bundle = BotIdentityBundle(bot_id="reverse-bot", identity="RE specialist")
    id_mgr.create_version("reverse-bot", bundle=bundle, activate=True)

    class MockAIAgent:
        bot_id = None
        identity_bundle = None

    agent = MockAIAgent()
    success = bridge.route_turn_to_specialist(agent, "reverse-bot", store=store)

    assert success is True
    assert agent.bot_id == "reverse-bot"
    assert agent.identity_bundle == bundle


def test_phase_2d_route_turn_to_specialist_nonexistent_bot():
    """Tentativa de rotear para bot inexistente não quebra o agente."""
    store = EventStore()
    class MockAIAgent:
        bot_id = "initial"
        identity_bundle = None

    agent = MockAIAgent()
    success = bridge.route_turn_to_specialist(agent, "unknown-bot", store=store)

    assert success is True
    assert agent.bot_id == "unknown-bot"
    assert agent.identity_bundle is None


def test_phase_2d_route_turn_to_specialist_none_agent():
    """Agente nulo retorna False com segurança."""
    assert bridge.route_turn_to_specialist(None, "reverse-bot") is False
