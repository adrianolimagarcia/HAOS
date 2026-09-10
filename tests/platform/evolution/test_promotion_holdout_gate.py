"""Gate held-out do Ouroboros: medir nas duas árvores e recusar regressão não-otimizada.

Invariantes (E2E, imports reais):

- o gate de PRODUÇÃO roda as suítes reais do fork contra a árvore real, mede os DOIS
  conjuntos (held-in e held-out) e aceita um candidato que não regride — sem evidência
  nos dois lados ele recusaria, então o aceite prova que a medição aconteceu;
- um candidato que regride UM caso que o split pôs no held-out é recusado, citando o
  caso na evidência — o ponto do gate: o candidato não pode ser promovido por ter
  otimizado o conjunto que ele viu;
- árvore ausente ⇒ recusa (fail-closed), nunca carimbo.
"""

from __future__ import annotations

from pathlib import Path

from hermes.platform.evals.holdout_split import split_cases
from hermes.platform.evals.runner import EvalCase, EvalSuite
from hermes.platform.evolution.promotion_holdout_gate import (
    HoldoutPromotionGate,
    default_repo_root,
)

REGRESSOR = "case-that-only-the-candidate-breaks"
CASES = [REGRESSOR] + [f"case-{i}" for i in range(4)]


def test_real_gate_measures_both_buckets_and_accepts_a_non_regressing_candidate():
    """Produção: suítes reais do fork, árvore real, candidato idêntico ao baseline."""
    verdict = HoldoutPromotionGate().evaluate(default_repo_root(), default_repo_root())

    assert verdict.accepted, verdict.reason
    # O aceite só vale porque houve medição comparável nos DOIS conjuntos: sem held-out
    # medido o próprio split recusa, então estes números são a prova de que rodou.
    assert verdict.checked_held_in >= 1, verdict.to_dict()
    assert verdict.checked_held_out >= 1, verdict.to_dict()
    assert not verdict.unbaselined, verdict.to_dict()
    assert verdict.evidence == ()
    assert "platform.fork" in " ".join(verdict.suite_ids)


def test_regression_in_a_held_out_case_is_refused_and_a_missing_tree_is_fail_closed(tmp_path):
    suite = EvalSuite(id="synthetic", cases=[EvalCase(id=cid, input={}) for cid in CASES])

    def case_runner(root: Path, case: EvalCase):
        # O candidato quebra UM caso; o baseline passa em todos. Qual caso cai no
        # held-out é decisão do split (seed), não do teste.
        broke = root.name == "candidate" and case.id == REGRESSOR
        return {"passed": not broke, "score": 0.0 if broke else 1.0, "meta": {}}

    # Achar um seed que ponha o caso regredido no held-out, e PROVAR que ele está lá:
    # um teste que mede o held-in passaria verde sem exercitar o gate.
    ids = [f"synthetic:{cid}" for cid in CASES]
    for seed in range(50):
        split = split_cases(ids, seed=seed, holdout_fraction=0.3)
        if f"synthetic:{REGRESSOR}" in split.held_out:
            break
    else:  # pragma: no cover - o split sempre põe algum caso no held-out
        raise AssertionError("nenhum seed pôs o caso regredido no held-out")
    assert f"synthetic:{REGRESSOR}" in split.held_out

    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()

    gate = HoldoutPromotionGate(suites=[suite], case_runner=case_runner, seed=seed)
    verdict = gate.evaluate(baseline, candidate)

    assert not verdict.accepted, verdict.reason
    assert any(REGRESSOR in line and "held_out" in line for line in verdict.evidence), (
        verdict.evidence
    )
    # A recusa veio do held-out: nenhum caso do held-in regrediu.
    assert not any("held_in" in line for line in verdict.evidence), verdict.evidence
    # Árvore ausente não vira carimbo: o gate recusa o que não conseguiu medir.
    assert not gate.evaluate(baseline, None).accepted
    assert not gate.evaluate(None, candidate).accepted
    assert not gate.evaluate(baseline, tmp_path / "nao-existe").accepted


def test_cycle_consults_the_gate_and_does_not_activate_a_refused_candidate(tmp_path):
    """A fiação: recusa do gate PARA o ciclo antes da ativação.

    Sem isto, o gate poderia estar ligado no lugar errado e ninguém notaria — o aceite
    (teste acima, e o e2e do mission demo) passaria igual.
    """
    from hermes.platform.evolution.ouroboros_lifecycle import OuroborosLifecycleManager
    from hermes.platform.skills.procedural_engine import (
        SkillGenerator,
        SkillRegistry,
        TaskExecutionRecord,
    )
    from hermes.platform.skills.procedural_evaluator import build_evaluated_pipeline

    baseline = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    baseline.mkdir()
    worktree.mkdir()

    class _WorktreeManager:
        """Worktree de verdade (dois diretórios), recortado de ``baseline``."""

        repo_root = baseline

        def __init__(self):
            self.removed = []

        def create_worktree(self, task_id, base_branch=None):
            return worktree

        def remove_worktree(self, task_id):
            self.removed.append(task_id)

        def get_diff(self, task_id):
            return ""

    wrk = _WorktreeManager()
    suite = EvalSuite(id="synthetic", cases=[EvalCase(id=cid, input={}) for cid in CASES])

    def case_runner(root: Path, case: EvalCase):
        broke = Path(root).name == "worktree" and case.id == REGRESSOR
        return {"passed": not broke, "score": 0.0 if broke else 1.0, "meta": {}}

    ids = [f"synthetic:{cid}" for cid in CASES]
    seed = next(
        s
        for s in range(50)
        if f"synthetic:{REGRESSOR}" in split_cases(ids, seed=s, holdout_fraction=0.3).held_out
    )

    registry = SkillRegistry()
    manager = OuroborosLifecycleManager(
        skill_registry=registry,
        skill_generator=SkillGenerator(min_pattern_frequency=2),
        skill_pipeline=build_evaluated_pipeline(
            registry=registry, available_capabilities={"terminal", "docker"}
        ),
        worktree_manager=wrk,
        promotion_threshold=0.80,
        min_improvement_pct=0.05,
        repo_root=baseline,
        holdout_gate=HoldoutPromotionGate(suites=[suite], case_runner=case_runner, seed=seed),
    )
    history = [
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

    result = manager.simulate_evolution_cycle(
        task_history=history,
        target_task_name="deploy_service",
        skill_name="gated-deploy-skill",
        candidate_eval_score=0.95,
        baseline_score=0.70,
    )

    assert result.success is False, result
    assert result.stage == "holdout_gate", result
    assert result.promoted is False
    assert "held_out" in result.error and REGRESSOR in result.error, result.error
    assert result.proposal.status == "rejected"
    # Não ativou: a skill recusada não existe no registry, e o worktree foi devolvido.
    assert registry.get("gated-deploy-skill") is None
    assert wrk.removed, "o worktree do candidato recusado deve ser removido"
