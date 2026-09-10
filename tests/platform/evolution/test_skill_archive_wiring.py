"""Fiação do arquivo de linhagens na produção (Ponto 5).

Invariantes (E2E, imports reais, ``HERMES_HOME`` temporário, store de verdade em disco):

- o ciclo de evolução que PROMOVE uma skill grava a variante no arquivo, e o
  ``dry_run`` — que não promove — não grava nada;
- o curador que ARQUIVA uma skill tira as variantes dela da seleção de pai.

Sem estes testes a fiação seria código morto que passa despercebida: os dois
ganchos são fail-soft de propósito (store quebrado nunca derruba promoção nem
poda), então um gancho ligado no lugar errado não levanta — só não arquiva nada.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hermes.platform.evolution.ouroboros_lifecycle import OuroborosLifecycleManager
from hermes.platform.evolution.skill_archive import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    SkillArchive,
    default_skill_archive_path,
    record_promoted_skill,
)
from hermes.platform.skills.procedural_engine import (
    SkillGenerator,
    SkillRegistry,
    TaskExecutionRecord,
)
from hermes.platform.skills.procedural_evaluator import build_evaluated_pipeline
from hermes.platform.skills.spec import SkillSpec

SKILL = "aged-deploy-skill"
GOOD_SCRIPT = "def execute_skill(context):\n    return {'status': 'ok'}\n"


def _spec(**overrides) -> SkillSpec:
    data = {
        "name": SKILL,
        "description": "Merges PDFs into one file.",
        "version": "1.0.0",
        "author": "Adriano",
        "license": "MIT",
        "status": "active",
        "capabilities_required": ["fs:read"],
        "entry_script": GOOD_SCRIPT,
    }
    data.update(overrides)
    spec = SkillSpec(**data)
    spec.calculate_checksum(spec.entry_script or spec.description or "")
    return spec


def _history() -> list[TaskExecutionRecord]:
    """Três execuções do MESMO fluxo: é a recorrência que o ciclo exige."""
    return [
        TaskExecutionRecord(
            task_name="deploy_service",
            action_sequence=["git_pull", "build_docker", "health_check"],
            success=True,
            context_keys=["cluster_env"],
            capabilities_used=["terminal"],
            duration_sec=40.0,
            metadata={"token_cost": 0.02},
        )
        for _ in range(3)
    ]


def _cycle(tmp_path, *, dry_run: bool):
    registry = SkillRegistry()
    generator = SkillGenerator(min_pattern_frequency=2)
    pipeline = build_evaluated_pipeline(
        registry=registry, available_capabilities={"terminal", "docker"}
    )
    manager = OuroborosLifecycleManager(
        skill_registry=registry,
        skill_generator=generator,
        skill_pipeline=pipeline,
        promotion_threshold=0.80,
        min_improvement_pct=0.05,
    )
    return manager.simulate_evolution_cycle(
        task_history=_history(),
        target_task_name="deploy_service",
        skill_name=SKILL,
        candidate_eval_score=0.92,
        baseline_score=0.70,
        dry_run=dry_run,
    )


@pytest.fixture
def curator_env(tmp_path, monkeypatch):
    """``HERMES_HOME`` isolado + curator/skill_usage recarregados (padrão de tests/agent/test_curator.py)."""
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


def test_promoted_cycle_records_variant_and_dry_run_does_not(tmp_path):
    result = _cycle(tmp_path, dry_run=False)
    assert result.success, result.error
    assert result.promoted

    archive = SkillArchive(default_skill_archive_path())
    variants = archive.variants(skill=SKILL)
    assert len(variants) == 1, (
        "o ciclo promoveu uma skill e nada foi arquivado: o gancho de promoção "
        "não está ligado (record_promoted_skill em ouroboros_lifecycle)"
    )
    recorded = variants[0]
    # Primeira variante desta skill: raiz de linhagem, geração 1, selecionável.
    assert recorded.lineage_root == recorded.variant_id
    assert recorded.generation == 0  # raiz de linhagem: nenhum ancestral
    assert recorded.status == STATUS_ACTIVE
    assert archive.best_variant(skill=SKILL, include_archived=False) is not None

    # dry_run não promove: o arquivo é registro do que está NO AR, não de tentativa.
    assert _cycle(tmp_path, dry_run=True).stage == "dry_run_completed"
    assert len(archive.variants(skill=SKILL)) == 1


def test_curator_archive_removes_variant_from_parent_selection(curator_env):
    curator, usage, home = curator_env["curator"], curator_env["usage"], curator_env["home"]
    decision = record_promoted_skill(_spec())
    assert decision is not None and decision.accepted, decision
    variant_id = decision.variant_id

    # Skill ``created_by: agent`` com atividade velha o bastante para ser arquivada.
    skill_dir = home / "skills" / SKILL
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {SKILL}\ndescription: x\n---\n", encoding="utf-8"
    )
    ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    data = usage.load_usage()
    data[SKILL] = usage._empty_record()
    data[SKILL].update(
        created_by="agent", created_at=ts, last_used_at=ts, last_activity_at=ts, use_count=1
    )
    usage.save_usage(data)

    counts = curator.apply_automatic_transitions()
    assert counts["archived"] == 1, counts

    archive = SkillArchive(default_skill_archive_path())
    assert archive.get(variant_id).status == STATUS_ARCHIVED, (
        "o curador arquivou a skill mas a variante continua ativa no arquivo: "
        "o gancho _sync_archive_status não está ligado"
    )
    # Arquivar tira da seleção de pai sem apagar: a linha continua legível.
    assert archive.best_variant(skill=SKILL, include_archived=False) is None
    assert len(archive.variants(skill=SKILL, status=STATUS_ARCHIVED)) == 1
