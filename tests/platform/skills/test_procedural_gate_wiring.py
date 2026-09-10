"""Gate de promoção de skills: medido, persistente e fail-closed (P2).

Contratos provados aqui:
- Sem avaliador e sem nota, a promoção é RECUSADA (antes: `spec.eval_score or 1.0`
  promovia tudo com nota 1.0 — auto-pass silencioso).
- Com o avaliador de produção registrado, a nota é MEDIDA (fração de checks
  aprovados) e um defeito estrutural barra a skill antes do gate de score.
- O registry do perfil ativo persiste em disco: uma skill registrada num processo
  é vista por outro (antes o registry era um dict por processo, e por isso
  `haos skills promote` nunca encontrava a skill que o comando anterior promoveu).
"""

from __future__ import annotations

import pytest

from hermes.platform.skills.procedural_engine import (
    SkillLifecyclePipeline,
    SkillRegistry,
    default_procedural_registry,
)
from hermes.platform.skills.procedural_evaluator import build_evaluated_pipeline
from hermes.platform.skills.spec import SkillSpec

GOOD_SCRIPT = "def execute_skill(context):\n    return {'status': 'ok'}\n"


def _spec(**overrides) -> SkillSpec:
    data = {
        "name": "pdf-merger",
        "description": "Merges PDFs into one file.",
        "version": "1.0.0",
        "author": "Adriano",
        "license": "MIT",
        "status": "candidate",
        "capabilities_required": ["fs:read"],
        "entry_script": GOOD_SCRIPT,
    }
    data.update(overrides)
    spec = SkillSpec(**data)
    spec.calculate_checksum(spec.entry_script or spec.description or "")
    return spec


def test_promotion_is_refused_without_any_measurement():
    """Fail-closed: nenhuma medição disponível ⇒ nenhuma promoção."""
    registry = SkillRegistry()
    spec = _spec()
    spec.status = "eval"

    ok, msg = SkillLifecyclePipeline(registry=registry).evaluate_and_activate(spec)

    assert ok is False
    assert "Nenhum avaliador" in msg
    assert spec.status == "eval", "skill sem medição não pode virar active"
    assert registry.get("pdf-merger") is None


def test_measured_score_decides_and_structural_defect_blocks():
    """A nota vem da medição; defeito estrutural barra antes do gate de score."""
    registry = SkillRegistry()
    pipeline = build_evaluated_pipeline(
        registry=registry, available_capabilities={"fs:read"}
    )

    good = _spec()
    ok, msg = pipeline.run_full_pipeline(good)
    assert ok is True, msg
    assert good.status == "active"
    # 6 checks, todos aprovados ⇒ nota medida é 1.0, não um default assumido.
    assert good.eval_score == pytest.approx(1.0)
    assert registry.get("pdf-merger").status == "active"

    # Defeito estrutural (script não compila): barra no sandbox, nunca chega ao gate.
    broken = _spec(name="broken-skill", entry_script="def execute_skill(:\n    pass\n")
    ok, msg = pipeline.run_full_pipeline(broken)
    assert ok is False
    assert "executable_evidence" in msg
    assert broken.status != "active"

    # Dois defeitos de qualidade (descrição longa com marketing + nenhuma
    # capability declarada) ⇒ nota medida 4/6 = 0.67, abaixo do limite 0.80:
    # o gate de score recusa a ativação.
    wordy = _spec(
        name="wordy-skill",
        description="A powerful and comprehensive seamless tool for merging many PDFs.",
        capabilities_required=[],
    )
    ok, msg = pipeline.run_full_pipeline(wordy)
    assert ok is False
    assert wordy.status != "active"
    assert wordy.eval_score == pytest.approx(4 / 6)
    assert wordy.eval_score < 0.80


def test_profile_registry_persists_across_instances(monkeypatch, tmp_path):
    """O registry do perfil ativo sobrevive ao processo (é o lado do disco)."""
    home = tmp_path / ".haos"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    default_procedural_registry().register(_spec())

    reopened = default_procedural_registry()
    stored = reopened.get("pdf-merger")
    assert stored is not None and stored.version == "1.0.0"
    assert (home / "skills" / "procedural_registry.json").exists()
