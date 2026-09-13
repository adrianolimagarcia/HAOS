"""Contract tests for @session:<id> and @session:last context reference expansion."""

from pathlib import Path
import pytest

from agent.context_references import (
    BUILTIN_PREFIXES,
    REFERENCE_PATTERN,
    preprocess_context_references,
)
from hermes_state import SessionDB


def test_session_in_builtin_prefixes():
    assert "session" in BUILTIN_PREFIXES


def test_reference_pattern_matches_session():
    m1 = REFERENCE_PATTERN.search("Please check @session:last for details")
    assert m1 is not None
    assert m1.group("kind") == "session"
    assert m1.group("value") == "last"

    m2 = REFERENCE_PATTERN.search("As we did in @session:abc1234 earlier")
    assert m2 is not None
    assert m2.group("kind") == "session"
    assert m2.group("value") == "abc1234"


def test_preprocess_context_references_expands_session(tmp_path: Path, monkeypatch):
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    (home / "state.db").parent.mkdir(parents=True, exist_ok=True)

    db = SessionDB()
    sid = "20260908_test_session_01"
    db.ensure_session(
        session_id=sid,
        source="cli",
        model="gemini-test",
    )
    db.set_session_title(sid, "Refatoração do Gateway")
    # Add a user message and assistant message
    db.append_message(
        session_id=sid,
        role="user",
        content="Como refatorar o gateway para suportar HAOS?",
    )
    db.append_message(
        session_id=sid,
        role="assistant",
        content="Implementado o UniversalProtocolGateway com testes unitários em 0.8s.",
    )
    db.close()

    # Preprocess a message containing @session:last
    res = preprocess_context_references("Veja o contexto de @session:last e me diga.", cwd=tmp_path, context_length=8192)
    assert res.expanded is True
    assert sid[:8] in res.message
    assert "Refatoração do Gateway" in res.message
    assert "UniversalProtocolGateway" in res.message


def test_preprocess_context_references_unknown_session(tmp_path: Path, monkeypatch):
    res = preprocess_context_references("Veja @session:nonexistent_uuid_xyz", cwd=tmp_path, context_length=8192)
    assert any("not found" in w.lower() for w in res.warnings)


def test_session_autocomplete_yields_session_completions(tmp_path: Path, monkeypatch):
    pytest.importorskip("prompt_toolkit")
    from hermes_cli.commands_completion import SlashCommandCompleter

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    db = SessionDB(db_path=home / "state.db")
    db.ensure_session(
        session_id="20260908_sess_auto_1",
        source="cli",
        model="gemini-test",
        title="Sessão de Autocomplete",
    )
    db.close()

    completer = SlashCommandCompleter.__new__(SlashCommandCompleter)
    completions = list(completer._context_completions("@session:"))
    texts = [c.text for c in completions]

    assert "@session:last" in texts
    assert any("20260908" in t for t in texts)
