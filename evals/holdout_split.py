"""Split determinístico held-in/held-out e veredito de aceitação para o loop de auto-melhoria.

WHY (o defeito que isto fecha): os gates atuais do HAOS não medem nada — aceitam a
nota que o próprio chamador informa (``ouroboros_lifecycle.evaluate_proposal``) e, sem
runner registrado, aprovam a skill com ``spec.eval_score if not None else 1.0``
(``procedural_engine.evaluate_and_activate``). Quem otimiza decide a nota.
Um conjunto reservado (``held_out``) que o otimizador NUNCA vê é o que separa
"corrigi o defeito" de "decorei os casos": a regressão nova só aparece nos casos que
ele não pôde ajustar. ``held_in`` é o conjunto que ele PODE ver e usar para corrigir.

WHY determinístico e estável no tempo: a atribuição depende só de ``(seed, case_id)``
— nunca da posição, da contagem ou da ordem da coleção. Isso dá três propriedades que
o loop precisa:
  1. mesma entrada + mesma seed ⇒ mesmo split, em qualquer máquina/processo, sem
     precisar persistir o split para reproduzi-lo;
  2. ADICIONAR casos novos não remaneja os antigos entre os conjuntos, então o
     held-out de hoje é comparável com o de ontem — um split re-sorteado a cada rodada
     faria o "não regrediu" de ontem não significar nada hoje;
  3. nenhum caso migra de held-out para held-in por acidente, que é o pior caso:
     o otimizador passaria a enxergar (e a decorar) exatamente o caso que media sua
     honestidade.

WHY puro/offline: sem rede, sem LLM, sem I/O e sem ``HERMES_HOME``. O veredito é
função apenas dos dados recebidos — qualquer coisa que o otimizador possa tocar não
pode entrar na conta.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Union

DEFAULT_SEED = 20260101
DEFAULT_HOLDOUT_FRACTION = 0.30

HELD_IN = "held_in"
HELD_OUT = "held_out"

# Um veredito é sobre um resultado por caso: o dict do EvalRunner
# ({"case_id", "passed", "score"}, ver hermes/platform/evals/runner.py:65-77), um
# mapa case_id → esse dict, ou um bool por caso.
CaseOutcome = Union[bool, Mapping[str, Any]]
CaseOutcomes = Union[Mapping[str, CaseOutcome], Iterable[Mapping[str, Any]]]


def bucket_of(case_id: str, seed: int = DEFAULT_SEED) -> float:
    """Posição determinística do caso em ``[0, 1)`` — função só de ``(seed, case_id)``.

    sha256, e não ``hash()``: o hash de bytes do Python é aleatorizado por processo
    (PYTHONHASHSEED), então ``hash()`` daria um split diferente a cada execução.
    """
    digest = hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


@dataclass(frozen=True)
class HoldoutSplit:
    """Dois conjuntos disjuntos de ids de caso (tuplas ordenadas, logo comparáveis)."""

    held_in: Tuple[str, ...]
    held_out: Tuple[str, ...]
    seed: int
    holdout_fraction: float

    def set_of(self, case_id: str) -> Optional[str]:
        """``HELD_IN``, ``HELD_OUT`` ou None quando o caso não pertence ao split."""
        if case_id in self.held_out:
            return HELD_OUT
        if case_id in self.held_in:
            return HELD_IN
        return None

    def is_held_out(self, case_id: str) -> bool:
        return case_id in self.held_out

    def to_dict(self) -> Dict[str, Any]:
        """Forma serializável para registrar o split junto da decisão (ledger/report)."""
        return {
            "seed": self.seed,
            "holdout_fraction": self.holdout_fraction,
            "held_in": list(self.held_in),
            "held_out": list(self.held_out),
        }


def split_cases(
    case_ids: Iterable[str],
    *,
    seed: int = DEFAULT_SEED,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
) -> HoldoutSplit:
    """Divide *case_ids* em held-in/held-out por hash de ``(seed, case_id)``.

    A ordem de entrada é irrelevante e ids repetidos não contam duas vezes.
    ``holdout_fraction`` é um LIMIAR por caso, não uma cota: o tamanho real de cada lado
    oscila em torno dele — é o preço (barato) de ter pertença estável por caso, e é o que
    permite comparar o held-out entre execuções.
    """
    if not 0.0 < holdout_fraction < 1.0:
        raise ValueError(f"holdout_fraction deve estar em (0, 1): {holdout_fraction!r}")

    ids = sorted({str(case_id) for case_id in case_ids})
    held_out = {case_id for case_id in ids if bucket_of(case_id, seed) < holdout_fraction}
    held_in = [case_id for case_id in ids if case_id not in held_out]

    # Um split degenerado (held-out vazio não detecta regressão nova; held-in vazio não
    # dá nada para o otimizador corrigir) é resolvido promovendo o caso de bucket mais
    # extremo — ainda determinístico, e sem remanejar ninguém quando o split já é
    # sadio, porque o ajuste só roda quando um dos lados ficou vazio.
    if len(ids) >= 2:
        if not held_out:
            held_out.add(max(ids, key=lambda cid: (bucket_of(cid, seed), cid)))
        elif not held_in:
            held_out.discard(min(ids, key=lambda cid: (bucket_of(cid, seed), cid)))
        held_in = [case_id for case_id in ids if case_id not in held_out]

    return HoldoutSplit(
        held_in=tuple(held_in),
        held_out=tuple(sorted(held_out)),
        seed=seed,
        holdout_fraction=holdout_fraction,
    )


@dataclass(frozen=True)
class CaseRegression:
    """Um caso que piorou do baseline para o candidato."""

    case_id: str
    set_name: str
    reason: str
    baseline_score: float
    candidate_score: float


@dataclass(frozen=True)
class AcceptanceVerdict:
    """Veredito do gate + a evidência por caso em que ele se apoia.

    ``accepted`` só é True quando os dois conjuntos têm evidência comparável e nenhum
    caso regrediu em nenhum deles.
    """

    accepted: bool
    reason: str
    held_in_regressions: Tuple[CaseRegression, ...] = ()
    held_out_regressions: Tuple[CaseRegression, ...] = ()
    checked_held_in: int = 0
    checked_held_out: int = 0
    unbaselined: Tuple[str, ...] = ()
    tolerance: float = 0.0

    @property
    def regressions(self) -> Tuple[CaseRegression, ...]:
        return self.held_in_regressions + self.held_out_regressions

    def evidence_lines(self) -> Tuple[str, ...]:
        """Uma linha por regressão, para o relatório/ledger explicar a recusa."""
        return tuple(
            f"{r.set_name}:{r.case_id} — {r.reason} "
            f"(baseline {r.baseline_score:.4f} → candidato {r.candidate_score:.4f})"
            for r in self.regressions
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "checked_held_in": self.checked_held_in,
            "checked_held_out": self.checked_held_out,
            "tolerance": self.tolerance,
            "unbaselined": list(self.unbaselined),
            "regressions": [
                {
                    "case_id": r.case_id,
                    "set": r.set_name,
                    "reason": r.reason,
                    "baseline_score": r.baseline_score,
                    "candidate_score": r.candidate_score,
                }
                for r in self.regressions
            ],
        }


def _index(outcomes: CaseOutcomes) -> Dict[str, CaseOutcome]:
    """Normaliza resultados por caso: mapa ``case_id → resultado`` ou lista do EvalRunner."""
    if isinstance(outcomes, Mapping):
        return {str(case_id): outcome for case_id, outcome in outcomes.items()}
    indexed: Dict[str, CaseOutcome] = {}
    for row in outcomes:
        if not isinstance(row, Mapping) or "case_id" not in row:
            raise ValueError(f"resultado por caso sem 'case_id': {row!r}")
        indexed[str(row["case_id"])] = row
    return indexed


def _state(outcome: CaseOutcome) -> Tuple[bool, float]:
    """``(passed, score)`` de um resultado. Ausente em um dict conta como falha/0.0, espelhando
    o default do EvalRunner; ``score`` omitido acompanha ``passed`` para não inventar regressão
    ao comparar com um baseline que traz nota explícita."""
    if isinstance(outcome, bool):
        return outcome, (1.0 if outcome else 0.0)
    passed = bool(outcome.get("passed", False))
    return passed, float(outcome.get("score", 1.0 if passed else 0.0))


def _regression(
    case_id: str, set_name: str, reason: str, baseline_score: float, candidate_score: float
) -> CaseRegression:
    return CaseRegression(
        case_id=case_id, set_name=set_name, reason=reason,
        baseline_score=baseline_score, candidate_score=candidate_score,
    )


def decide_acceptance(
    before: CaseOutcomes,
    after: CaseOutcomes,
    split: HoldoutSplit,
    *,
    tolerance: float = 0.0,
) -> AcceptanceVerdict:
    """Aceita a mudança SÓ se não houver regressão nem no held-in nem no held-out.

    *before*/*after* são os resultados por caso antes e depois (mapa ou lista do
    EvalRunner). Um caso que sumiu do candidato conta como regressão — apagar o caso
    que media a falha é a forma mais barata de "melhorar" a nota. Um caso que só
    aparece no candidato não é regressão (não há baseline para comparar) e fica
    registrado em ``unbaselined``.

    Aceitar exige evidência comparável nos DOIS conjuntos: com o held-out vazio (ou
    todo ele sem baseline) o gate não teria como detectar regressão nova, e um
    "aceito" nessas condições seria carimbo, não medição.
    """
    baseline, candidate = _index(before), _index(after)

    missing = [
        name for name, cases in ((HELD_IN, split.held_in), (HELD_OUT, split.held_out)) if not cases
    ]
    if missing:
        return AcceptanceVerdict(
            accepted=False,
            reason=(
                "evidência insuficiente: conjunto(s) vazio(s) "
                f"{', '.join(missing)} — sem held-out não há como detectar regressão nova, "
                "e sem held-in não há nada que o otimizador pudesse corrigir com evidência"
            ),
            tolerance=tolerance,
        )

    held_in_regressions, held_out_regressions, unbaselined = [], [], []
    checked = {HELD_IN: 0, HELD_OUT: 0}
    for set_name, cases in ((HELD_IN, split.held_in), (HELD_OUT, split.held_out)):
        for case_id in cases:
            if case_id not in baseline:
                unbaselined.append(case_id)
                continue
            baseline_passed, baseline_score = _state(baseline[case_id])
            if case_id not in candidate:
                checked[set_name] += 1
                regression = _regression(
                    case_id, set_name, "ausente no resultado do candidato",
                    baseline_score, 0.0,
                )
            else:
                candidate_passed, candidate_score = _state(candidate[case_id])
                checked[set_name] += 1
                if baseline_passed and not candidate_passed:
                    regression = _regression(
                        case_id, set_name, "passou → falhou", baseline_score, candidate_score
                    )
                elif candidate_score < baseline_score - tolerance:
                    regression = _regression(
                        case_id, set_name,
                        f"score caiu mais que a tolerância ({tolerance})",
                        baseline_score, candidate_score,
                    )
                else:
                    regression = None
            if regression is not None:
                (held_in_regressions if set_name == HELD_IN else held_out_regressions).append(regression)

    unmeasured = [
        name for name, count in checked.items() if count == 0
    ]
    if unmeasured:
        return AcceptanceVerdict(
            accepted=False,
            reason=(
                "evidência insuficiente: nenhum caso comparável em "
                f"{', '.join(unmeasured)} (sem baseline para os casos do conjunto) — "
                "aceitar sem medição seria carimbo"
            ),
            checked_held_in=checked[HELD_IN],
            checked_held_out=checked[HELD_OUT],
            unbaselined=tuple(sorted(unbaselined)),
            tolerance=tolerance,
        )

    verdict = AcceptanceVerdict(
        accepted=not (held_in_regressions or held_out_regressions),
        reason="",
        held_in_regressions=tuple(held_in_regressions),
        held_out_regressions=tuple(held_out_regressions),
        checked_held_in=checked[HELD_IN],
        checked_held_out=checked[HELD_OUT],
        unbaselined=tuple(sorted(unbaselined)),
        tolerance=tolerance,
    )
    if verdict.accepted:
        reason = (
            f"sem regressão em {verdict.checked_held_in} caso(s) held-in e "
            f"{verdict.checked_held_out} held-out (tolerância {tolerance})"
        )
    else:
        reason = (
            f"regressão em {len(verdict.regressions)} caso(s) — "
            f"held_in: {', '.join(r.case_id for r in verdict.held_in_regressions) or 'nenhum'}; "
            f"held_out: {', '.join(r.case_id for r in verdict.held_out_regressions) or 'nenhum'}"
        )
    return replace(verdict, reason=reason)
