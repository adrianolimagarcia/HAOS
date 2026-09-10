"""O curador é um otimizador agendado: o funil de escrita e a passada que muta skills
obedecem ao manifesto de superfícies (``hermes/platform/evolution/surface_manifest.py``).

Invariante: alvo read-only é recusado com motivo (e nada é escrito), a superfície
editável do curador continua funcionando, e com a biblioteca de skills fora do
manifesto editável a poda não muta nada.
"""

from __future__ import annotations

import importlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hermes.platform.evolution import surface_manifest


@pytest.fixture
def curator_env(tmp_path, monkeypatch):
    """HERMES_HOME isolado + curator/skill_usage recarregados (padrão de tests/agent/test_curator.py)."""
    home = tmp_path / ".hermes"
    (home / "skills").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    import tools.skill_usage as usage
    importlib.reload(usage)
    import agent.curator as curator
    importlib.reload(curator)
    monkeypatch.setattr(curator, "_load_config", lambda: {})
    monkeypatch.setattr(usage, "_prune_builtins_enabled", lambda: False)
    return {"home": home, "curator": curator, "usage": usage}


def _aging_agent_skill(usage, skills_dir: Path, name: str, *, days: int = 200) -> None:
    """Skill ``created_by: agent`` com atividade velha o bastante para ser arquivada."""
    skill = skills_dir / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: x\n---\n", encoding="utf-8"
    )
    ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    data = usage.load_usage()
    data[name] = usage._empty_record()
    data[name].update(created_by="agent", created_at=ts, last_used_at=ts,
                      last_activity_at=ts, use_count=1)
    usage.save_usage(data)


def test_curator_refuses_read_only_targets_and_the_guard_is_load_bearing(curator_env, caplog):
    curator, usage, home = curator_env["curator"], curator_env["usage"], curator_env["home"]

    # Alvo read-only declarado (o store de sessões): nada é escrito e a recusa sai no
    # log com o motivo — quem lê o log sabe qual superfície barrou.
    with caplog.at_level(logging.WARNING, logger="agent.curator"):
        curator._write_file(home / "state.db", "run.json", {"a": 1})
    assert not (home / "state.db").exists()
    assert any(
        "refused write" in r.getMessage() and "state.db" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]

    # Superfície editável declarada (logs do curador): o fluxo atual de escrita segue intacto.
    report = home / "logs" / "curator" / "run.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    curator._write_file(report, "run.json", {"a": 1})
    assert json.loads(report.read_text(encoding="utf-8")) == {"a": 1}

    # Com a superfície de skills fora do manifesto editável, a passada que muta a
    # biblioteca (estado/arquivo/telemetria) não toca em nada...
    _aging_agent_skill(usage, home / "skills", "antiga")
    real_classify = surface_manifest.classify
    surface_manifest.classify = lambda path: surface_manifest.SurfaceDecision(
        path=Path(path), writable=False, surface=None, reason="teste: skills read-only"
    )
    try:
        counts = curator.apply_automatic_transitions()
        assert counts["checked"] == 0 and counts["archived"] == 0
        assert usage.load_usage()["antiga"]["state"] == usage.STATE_ACTIVE
        # ...e o fork que muta skills via skill_manage nem chega a ser disparado.
        summary, meta = curator._consolidation_pass("auto: ", "no changes", False, set())
        assert "skipped (skills surface not writable)" in summary
        assert meta["tool_calls"] == [] and meta["error"] is None
    finally:
        surface_manifest.classify = real_classify

    # ...e volta a podar quando a superfície é a declarada como editável: a guarda é
    # real (muda o comportamento), não um comentário.
    assert curator.apply_automatic_transitions()["archived"] == 1
    assert usage.load_usage()["antiga"]["state"] == usage.STATE_ARCHIVED
