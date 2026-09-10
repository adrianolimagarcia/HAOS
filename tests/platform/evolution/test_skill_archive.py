"""Arquivo de linhagens de skills: admissão não-monotônica e seleção balanceada (Ponto 5).

Contratos provados aqui (E2E, imports reais, ``HERMES_HOME`` temporário e store de
verdade em disco):

- ``test_worse_child_enters_archive_but_loses_best_of_lineage`` — um filho que
  PIORA a nota entra no arquivo (o gate de admissão é de VALIDADE, não de score) e
  ainda assim perde a leitura de melhor variante; o mesmo filho é recusado pelo
  gate de PRODUÇÃO (``min_eval_score``), provando que as duas perguntas são
  diferentes. Arquivar não deleta: a linha continua legível e contada.
- ``test_children_count_penalizes_parent_and_seeded_selection_is_reproducible`` —
  ``children_count`` derruba o peso do pai na PRÓXIMA seleção (ele perde fatia de
  sorteios, sem virar inelegível), e a seleção é reproduzível dado um
  ``random.Random`` semeado injetado.
"""

from __future__ import annotations

import random
import sqlite3

import pytest

from hermes.platform.evolution.skill_archive import (
    STATUS_ARCHIVED,
    default_skill_archive,
    default_skill_archive_path,
    mark_skill_archived,
    selection_weight,
)
from hermes.platform.skills.procedural_engine import SkillRegistry
from hermes.platform.skills.procedural_evaluator import (
    DeterministicSkillEvaluator,
    build_evaluated_pipeline,
)
from hermes.platform.skills.spec import SkillSpec

GOOD_SCRIPT = "def execute_skill(context):\n    return {'status': 'ok'}\n"
SKILL = "pdf-merger"
# Descrição com termo de marketing + nenhuma capability declarada: dois checks de
# QUALIDADE reprovados, nenhum check crítico — a variante é pior, mas funciona.
WORSE_DESCRIPTION = "A powerful and comprehensive seamless tool for merging PDFs."


def _spec(**overrides) -> SkillSpec:
    data = {
        "name": SKILL,
        "description": "Merges PDFs into one file.",
        "version": "1.0.0",
        "author": "Adriano",
        "license": "MIT",
        "status": "eval",
        "capabilities_required": ["fs:read"],
        "entry_script": GOOD_SCRIPT,
    }
    data.update(overrides)
    spec = SkillSpec(**data)
    spec.calculate_checksum(spec.entry_script or spec.description or "")
    return spec


def _worse_spec(version: str = "0.9.0") -> SkillSpec:
    return _spec(version=version, description=WORSE_DESCRIPTION, capabilities_required=[])


def _draws(archive, count: int, seed: int) -> list:
    """Sorteios de pai com o MESMO gerador semeado (reproduzível por construção)."""
    rng = random.Random(seed)
    return [archive.select_parent(skill=SKILL, rng=rng).variant_id for _ in range(count)]


@pytest.fixture
def home(monkeypatch, tmp_path):
    """HERMES_HOME temporário: o store real é criado dentro do tmp, nunca em ~/.hermes."""
    root = tmp_path / ".haos"
    monkeypatch.setenv("HERMES_HOME", str(root))
    return root


def test_worse_child_enters_archive_but_loses_best_of_lineage(home):
    archive = default_skill_archive()

    parent = archive.record(_spec())
    assert parent.accepted is True and parent.parent_id is None
    assert parent.accepted_below_parent is False, "a raiz não está abaixo de ninguém"

    child = archive.record(_worse_spec(), parent_id=parent.variant_id)
    assert child.accepted is True, child.reason
    assert child.passed_gate is True
    assert child.accepted_below_parent is True, "pior que o pai e admitida mesmo assim"
    assert child.eval_score < parent.eval_score
    # A nota registrada é a MEDIDA pelo gate de validade de produção, não um valor declarado.
    assert child.eval_score == pytest.approx(
        DeterministicSkillEvaluator()(_worse_spec()).score)

    # O gate de PRODUÇÃO recusa a mesma variante (nota abaixo de min_eval_score): a
    # admissão no arquivo e a promoção respondem perguntas diferentes.
    worse = _worse_spec()
    promoted, message = build_evaluated_pipeline(
        registry=SkillRegistry(), available_capabilities={"fs:read"}
    ).evaluate_and_activate(worse)
    assert promoted is False and worse.status == "eval", message

    # A ordenação por nota é da SELEÇÃO: a pior variante está no arquivo e não é a melhor.
    best = archive.best_variant(lineage_root=parent.variant_id)
    assert best.variant_id == parent.variant_id
    assert best.eval_score > child.eval_score
    assert [v.variant_id for v in archive.lineage(parent.variant_id)] == [
        parent.variant_id, child.variant_id]
    assert archive.get(child.variant_id).generation == 1
    assert archive.get(parent.variant_id).children_count == 1
    assert [v.variant_id for v in archive.children(parent.variant_id)] == [child.variant_id]

    # Persistência real: outra instância (outra conexão) vê a mesma genealogia.
    reopened = default_skill_archive()
    assert default_skill_archive_path() == home / "memory" / "skill_archive.db"
    assert default_skill_archive_path().exists()
    assert [v.variant_id for v in reopened.lineage(parent.variant_id)] == [
        parent.variant_id, child.variant_id]
    assert not (home / "state.db").exists(), "o arquivo de variantes não mora no state.db"
    columns = {
        row[1] for row in sqlite3.connect(
            default_skill_archive_path()).execute("PRAGMA table_info(skill_variants)")
    }
    assert {"skill", "variant_id", "parent_id", "lineage_root", "content_hash",
            "eval_score", "children_count", "status", "created_at"} <= columns

    # Arquivar é o TETO destrutivo (o mesmo do curador): tira da seleção, não do arquivo.
    assert mark_skill_archived(SKILL, archive=archive) == 2
    assert archive.count(SKILL) == 2, "nenhuma variante é deletada ao arquivar"
    assert archive.get(child.variant_id).status == STATUS_ARCHIVED
    assert archive.lineage(parent.variant_id)[1].variant_id == child.variant_id
    assert archive.select_parent(skill=SKILL) is None, "arquivada não gera a próxima geração"
    assert archive.select_parent(
        skill=SKILL, include_archived=True, rng=random.Random(3)) is not None
    assert default_skill_archive().count(SKILL) == 2, "as duas linhas continuam em disco"


def test_children_count_penalizes_parent_and_seeded_selection_is_reproducible(home):
    archive = default_skill_archive()

    strong = archive.record(_spec())
    weak = archive.record(_worse_spec())
    assert strong.accepted and weak.accepted
    strong_before = archive.get(strong.variant_id)
    weak_before = archive.get(weak.variant_id)
    assert strong_before.lineage_root == strong_before.variant_id, "raiz da própria linhagem"
    assert strong_before.lineage_root != weak_before.lineage_root, (
        "dois ramos independentes da mesma skill")

    # Antes de gerar filhos: nota maior ⇒ peso maior (mesmo children_count = 0).
    assert strong_before.children_count == weak_before.children_count == 0
    assert selection_weight(strong_before) > selection_weight(weak_before)

    before = _draws(archive, count=400, seed=1234)
    share_before = before.count(strong.variant_id) / len(before)
    assert len(set(before)) == 2, "seleção com reposição sorteia do arquivo inteiro"
    assert share_before > 0

    # Três gerações saem do pai forte; ele é penalizado na PRÓXIMA seleção.
    for index, version in enumerate(("1.1.0", "1.2.0", "1.3.0"), start=1):
        generated = archive.record(_spec(version=version), parent_id=strong.variant_id)
        assert generated.accepted is True, generated.reason
        assert generated.parent_id == strong.variant_id
    penalized = archive.get(strong.variant_id)
    assert penalized.children_count == 3
    assert selection_weight(penalized) < selection_weight(strong_before), (
        "children_count penaliza o peso do pai")
    assert archive.get(penalized.variant_id).eval_score == strong_before.eval_score, (
        "a penalidade é por filhos gerados, não por nota rebaixada")

    after = _draws(archive, count=400, seed=1234)
    share_after = after.count(strong.variant_id) / len(after)
    assert share_after < share_before, "o pai sobre-explorado perde fatia de sorteios"
    assert share_after > 0, "penalizar não é excluir: o pai continua elegível"
    assert set(after) <= {v.variant_id for v in archive.variants(skill=SKILL)}

    # Leitura de melhor variante POR LINHAGEM: a skill tem dois ramos e cada ramo
    # reporta o seu melhor. Empate de nota desempata pelo antepassado (o mais antigo),
    # então a raiz forte continua sendo o melhor do ramo dela, não um filho igual.
    roots = archive.lineage_roots(SKILL)
    assert set(roots) == {strong_before.lineage_root, weak_before.lineage_root}
    assert {root: archive.best_variant(lineage_root=root).variant_id for root in roots} == {
        strong_before.lineage_root: strong.variant_id,
        weak_before.lineage_root: weak.variant_id,
    }
    assert [v.variant_id for v in archive.lineage(strong_before.lineage_root)][0] == strong.variant_id

    # Reproduzibilidade: o resultado é função do rng INJETADO (nada de sorteio global).
    assert _draws(archive, count=50, seed=99) == _draws(archive, count=50, seed=99)
    rng_a, rng_b = random.Random(7), random.Random(7)
    assert [archive.select_parent(skill=SKILL, rng=rng_a).variant_id for _ in range(20)] == [
        archive.select_parent(skill=SKILL, rng=rng_b).variant_id for _ in range(20)]
    assert archive.select_parent(skill="skill-inexistente") is None
