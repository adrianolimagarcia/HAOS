"""Gate de promoção held-out: o candidato não pode regredir no que não otimizou.

O ciclo do Ouroboros mede o candidato numa árvore isolada (o worktree). Medir só o
número agregado que o próprio candidato pode ter otimizado é exatamente o buraco que o
split existe para fechar: aqui a MESMA suíte roda nas duas árvores (repo = baseline,
worktree = candidato), os casos são partidos em held-in/held-out por hash de
``(seed, case_id)`` — estável, insensível à ordem, e um caso não migra para o held-in
depois de já ter sido usado — e a promoção só passa se nenhum caso regredir em NENHUM
dos dois conjuntos.

Fail-closed: árvore ausente, suíte vazia, caso ausente de um dos lados ou held-out sem
evidência comparável ⇒ recusa com motivo. Um gate que carimba quando não mediu é pior
que não ter gate — foi o defeito do Ponto 2.

O que este gate NÃO mede, dito na cara: a QUALIDADE da skill. As suítes reais do fork
(``platform.fork.*``) medem o contrato da ÁRVORE — compila, respeita PEP-420, não importa
fora da stdlib no topo, os seams canônicos importam, a rota exata de modelo sobrevive.
Ou seja: ele recusa um candidato cuja árvore quebra o contrato do fork, e é a única
suíte real que existe hoje. Suíte nova entra por injeção (``suites=``); é para isso que
``EvalSuite`` é dado, e o minerador de falhas já emite casos a partir de falha real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hermes.platform.evals.holdout_split import (
    DEFAULT_HOLDOUT_FRACTION,
    DEFAULT_SEED,
    decide_acceptance,
    split_cases,
)
from hermes.platform.evals.runner import EvalRunner, EvalSuite
from hermes.platform.evals.suites_platform import build_platform_suites, run_fork_case

# Assinatura do runner de caso: ``(árvore, caso) -> {"passed", "score", "meta"}``.
CaseRunner = Callable[[Path, Any], Dict[str, Any]]


@dataclass(frozen=True)
class HoldoutVerdict:
    """Veredito do gate, com a evidência que o receipt deve citar.

    ``evidence`` já vem em linha pronta (``held_out:caso — passou → falhou``) porque o
    motivo da recusa precisa ser legível no log de quem só vê o update passar.
    """

    accepted: bool
    reason: str
    evidence: Tuple[str, ...] = ()
    checked_held_in: int = 0
    checked_held_out: int = 0
    unbaselined: Tuple[str, ...] = ()
    case_ids: Tuple[str, ...] = ()
    held_out: Tuple[str, ...] = ()
    suite_ids: Tuple[str, ...] = ()

    def evidence_lines(self) -> Tuple[str, ...]:
        return self.evidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "checked_held_in": self.checked_held_in,
            "checked_held_out": self.checked_held_out,
            "unbaselined": list(self.unbaselined),
            "cases": list(self.case_ids),
            "held_out": list(self.held_out),
            "suites": list(self.suite_ids),
        }


class HoldoutPromotionGate:
    """Roda a suíte nas duas árvores e decide pela régua held-in/held-out.

    ``suites=None`` usa as suítes reais do fork (``build_platform_suites``); ``runner`` e
    ``case_runner`` existem para injeção — o gate em si não sabe o que é um caso, só
    compara dois conjuntos de resultados por ``case_id``.
    """

    def __init__(
        self,
        *,
        suites: Optional[Sequence[EvalSuite]] = None,
        runner: Optional[EvalRunner] = None,
        case_runner: Optional[CaseRunner] = None,
        seed: int = DEFAULT_SEED,
        holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
        tolerance: float = 0.0,
    ) -> None:
        self.suites = list(suites) if suites is not None else None
        self.runner = runner or EvalRunner()
        self.case_runner = case_runner or run_fork_case
        self.seed = seed
        self.holdout_fraction = holdout_fraction
        self.tolerance = tolerance

    # ------------------------------------------------------------------ #
    # medição
    # ------------------------------------------------------------------ #

    def _measure(self, root: Path, suites: Sequence[EvalSuite]) -> Dict[str, Dict[str, Any]]:
        """Resultados por ``suíte:caso`` para UMA árvore."""
        measured: Dict[str, Dict[str, Any]] = {}
        for suite in suites:
            result = self.runner.run_suite(
                suite,
                lambda case, r=root: self.case_runner(r, case),
                label=str(root),
            )
            for outcome in result.outcomes:
                measured[f"{suite.id}:{outcome.get('case_id')}"] = outcome
        return measured

    def evaluate(
        self,
        baseline_root: Optional[Path],
        candidate_root: Optional[Path],
    ) -> HoldoutVerdict:
        """Veredito para um candidato medido contra o baseline. Nunca levanta."""
        for role, root in (("baseline", baseline_root), ("candidato", candidate_root)):
            if root is None:
                return HoldoutVerdict(
                    accepted=False,
                    reason=(
                        f"{role} sem árvore: não há o que medir. Um candidato só é "
                        "promovível medido contra uma árvore isolada."
                    ),
                )
            if not Path(root).is_dir():
                return HoldoutVerdict(
                    accepted=False,
                    reason=f"{role} não é um diretório: {root}",
                )

        suites = self.suites if self.suites is not None else build_platform_suites(Path(baseline_root))
        if not suites:
            return HoldoutVerdict(accepted=False, reason="nenhuma suíte configurada: nada foi medido")

        before = self._measure(Path(baseline_root), suites)
        after = self._measure(Path(candidate_root), suites)
        case_ids = tuple(sorted(set(before) | set(after)))
        if not case_ids:
            return HoldoutVerdict(accepted=False, reason="as suítes não produziram nenhum caso")

        split = split_cases(
            case_ids, seed=self.seed, holdout_fraction=self.holdout_fraction
        )
        verdict = decide_acceptance(before, after, split, tolerance=self.tolerance)
        return HoldoutVerdict(
            accepted=bool(verdict.accepted),
            reason=verdict.reason,
            evidence=tuple(verdict.evidence_lines()),
            checked_held_in=int(verdict.checked_held_in),
            checked_held_out=int(verdict.checked_held_out),
            unbaselined=tuple(verdict.unbaselined),
            case_ids=case_ids,
            held_out=tuple(split.held_out),
            suite_ids=tuple(suite.id for suite in suites),
        )


def default_repo_root() -> Path:
    """Raiz do checkout onde este módulo vive (``<repo>/hermes/platform/evolution/``)."""
    return Path(__file__).resolve().parents[3]


def default_holdout_gate() -> HoldoutPromotionGate:
    """Gate de produção: suítes reais do fork e a política do próprio módulo do split.

    ``seed``/``holdout_fraction`` vêm de ``hermes.platform.evals.holdout_split`` de
    propósito — duplicá-los aqui faria a política divergir em silêncio.
    """
    return HoldoutPromotionGate()


__all__ = [
    "CaseRunner",
    "HoldoutPromotionGate",
    "HoldoutVerdict",
    "default_holdout_gate",
    "default_repo_root",
]
