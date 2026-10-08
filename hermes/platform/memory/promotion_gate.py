"""Gate de cascata antes de promover licao -> skill (absorcao SIFT).

Fecha o "g1" que ``skill_promotion.py`` declarou nao-implementavel in-repo para
licoes arbitrarias: nenhuma regra sobe por contador cru. A cascata e barata e
fail-closed, em quatro estagios — para no primeiro que negar:

1. ``static``        — o veredito deterministico (``assess_lesson_promotion``)
                       precisa ser promovivel; nada aqui reabre um "ignore".
2. ``cheap_judge``   — uma chamada a lane ``review`` (juiz fraco): a licao e
                       procedural, reutilizavel e nao ja ensinada no parque?
3. ``strong_judges`` — as duas lanes de FAMILIAS diferentes (``review`` e
                       ``goal_judge``) precisam CONCORDAR em promover.
                       Discordancia ou resposta ilegivel implica bloqueio:
                       nunca fail-open — juiz que nao responde nao absolve.
4. ``eval_case``     — exige um caso de eval registrado como VERMELHO-ANTES
                       (``<home>/memory/skill_proposals/red_before/<key>.json``,
                       com ``case_id`` e ``exit_code`` diferente de zero) e
                       re-executa o runner do placar AGORA, exigindo exit 0
                       (verde-depois). O guard do placar e conferido antes
                       (fail-closed se o lock nao bater).

O gate NAO aplica skill: ele decide se a PROPOSTA (g7 — humano no volante) pode
existir. Bloqueio e contabilizado pelo chamador (``gate_blocked``), para a licao
bloqueada continuar visivel em vez de virar ruido silencioso.

Custo: 3 chamadas de juiz por licao (1 fraca + 2 fortes) na lane de aux; a lane
``review`` e reusada como juiz fraco e como um dos fortes de proposito — o sinal
caro nao e a chamada, e a concordancia cross-familia.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

if TYPE_CHECKING:  # sem import de runtime: evita ciclo skill_promotion <-> promotion_gate
    from hermes.platform.memory.skill_promotion import PromotionAssessment

logger = logging.getLogger(__name__)

# Juiz fraco = lane mais barata das duas. Juizes fortes = as duas familias
# distintas (gemini via ``review``, codex via ``goal_judge``): concordancia
# cross-familia e o sinal; a mesma familia concordaria por construcao.
CHEAP_JUDGE_TASK = "review"
STRONG_JUDGE_TASKS = ("review", "goal_judge")
JUDGE_TIMEOUT_S = 90.0
# Modelos com raciocinio gastam tokens de raciocinio ANTES do texto: teto baixo
# devolve finish_reason=length com content vazio (truncamento silencioso).
JUDGE_MAX_TOKENS = 512
# Teto do prompt da licao enviado ao juiz (a licao canonica e curta; cortar
# protege o orcamento sem mudar o criterio).
JUDGE_PROMPT_CHARS = 4000
EVAL_RUNNER_TIMEOUT_S = 600.0
EVAL_GUARD_TIMEOUT_S = 120.0
RED_BEFORE_DIRNAME = "red_before"

# Negacao explicita: 'promote' dentro de 'not promote' jamais promove.
_NEGATION_RE = re.compile(r"\b(?:not|nao|never|nunca)\b")
# Palavras isoladas (descarta pontuacao/markdown ao redor do token).
_WORD_RE = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class GateVerdict:
    """Veredito do gate: onde parou e por que (linhagem auditavel)."""

    allowed: bool
    stage: str
    reason: str
    lineage: Dict[str, Any] = field(default_factory=dict)


_JUDGE_SYSTEM = (
    "Voce e um juiz conservador de curadoria de skills. "
    "Na duvida, rejeite. Sem preambulo: a ultima linha deve ser o VEREDITO."
)


def _judge_prompt(fact: str) -> str:
    return (
        "Licao canonica candidata a virar skill:\n"
        + fact.strip()[:JUDGE_PROMPT_CHARS]
        + "\n\nPromova SOMENTE se TODAS valerem:\n"
        "- e procedural/reutilizavel (ensina um COMO fazer), nao um fato pontual;\n"
        "- ensina algo nao-obvio que ainda nao esteja coberto pelo parque de skills;\n"
        "- nao e especifica de uma unica sessao ou ocorrencia isolada.\n"
        "Uma duvida vale reject. Responda EXATAMENTE uma linha final:\n"
        "VEREDITO: promote\n"
        "ou\n"
        "VEREDITO: reject"
    )


def _parse_judge_verdict(raw: str) -> str:
    """Extrai 'promote' | 'reject' | '' (ilegivel). Vazio NUNCA promove."""
    text = (raw or "").strip()
    if not text:
        return ""
    low = text.lower()
    if "veredito:" in low:
        tail = low.split("veredito:")[-1]
        words = _WORD_RE.findall(tail)
        token = words[0] if words else ""
        if token.startswith("promote"):
            return "promote"
        if token.startswith("reject"):
            return "reject"
    has_promote = re.search(r"\bpromote\b", low) is not None
    has_reject = re.search(r"\breject\b", low) is not None
    if has_promote and not has_reject and not _NEGATION_RE.search(low):
        return "promote"
    if has_reject:
        return "reject"
    return ""


def _judge_verdict(judge: Callable[[str, str, float], str], task: str, fact: str) -> str:
    """Veredito de um juiz. Resposta vazia/ilegivel devolve '' (implica bloqueio)."""
    raw = judge(task, _judge_prompt(fact), JUDGE_TIMEOUT_S)
    verdict = _parse_judge_verdict(raw)
    logger.debug("promotion gate: juiz %s -> %r", task, verdict)
    return verdict


def _response_text(resp: Any) -> str:
    try:
        return str(resp.choices[0].message.content or "")
    except Exception:
        return ""


def _default_judge(task: str, prompt: str, timeout: float) -> str:
    """Juiz real: lane ``auxiliary.<task>`` via o cliente de aux sancionado."""
    from agent.auxiliary_client import call_llm  # function-level: nao fixa home no import

    resp = call_llm(
        task=task,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        max_tokens=JUDGE_MAX_TOKENS,
        timeout=timeout,
    )
    return _response_text(resp)


def _eval_scripts() -> tuple:
    """(runner do placar, guard do lock) sob <home> — a mesma raiz que o eval usa."""
    from hermes_constants import get_hermes_home  # function-level: resolve no call

    home = Path(get_hermes_home())
    return home / "evals" / "runner.py", home / "scripts" / "scoreboard_guard.py"


def _default_guard() -> int:
    """exit do guard do placar (0 = lock confere)."""
    _, guard_py = _eval_scripts()
    proc = subprocess.run(
        [sys.executable, str(guard_py), "verify"],
        capture_output=True,
        text=True,
        timeout=EVAL_GUARD_TIMEOUT_S,
    )
    return int(proc.returncode)


def _default_runner(case_id: str) -> int:
    """exit do runner do placar para um caso exato (0 = caso verde)."""
    runner_py, _ = _eval_scripts()
    proc = subprocess.run(
        [sys.executable, str(runner_py), "--case", case_id],
        capture_output=True,
        text=True,
        timeout=EVAL_RUNNER_TIMEOUT_S,
    )
    if proc.returncode != 0:
        logger.info("promotion gate: caso %r exit=%s", case_id, proc.returncode)
    return int(proc.returncode)


def red_before_path(key: str, home: Optional[Path] = None) -> Path:
    """Onde o vermelho-antes do candidato e registrado (convencao unica)."""
    from hermes_constants import get_hermes_home  # function-level: resolve no call

    root = Path(home) if home is not None else Path(get_hermes_home())
    return root / "memory" / "skill_proposals" / RED_BEFORE_DIRNAME / f"{key}.json"


def record_red_before(
    key: str,
    case_id: str,
    exit_code: int,
    *,
    home: Optional[Path] = None,
    command: str = "",
) -> Path:
    """Registra a execucao VERMELHA do caso ANTES da mudanca (a prova do g1).

    Recusa ``exit_code == 0``: registrar um verde como vermelho-antes e
    exatamente o atalho que o gate existe para impedir.
    """
    if not str(case_id).strip():
        raise ValueError("case_id obrigatorio")
    if exit_code == 0:
        raise ValueError("vermelho-antes exige exit_code != 0 (caso ja passava)")
    path = red_before_path(key, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "case_id": str(case_id),
                "exit_code": int(exit_code),
                "at": time.time(),
                "command": command or f"runner.py --case {case_id}",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def evaluate_promotion_gate(
    fact: str,
    assessment: "PromotionAssessment",
    *,
    key: str,
    home: Optional[Path] = None,
    judge: Optional[Callable[[str, str, float], str]] = None,
    runner: Optional[Callable[[str], int]] = None,
    guard: Optional[Callable[[], int]] = None,
) -> GateVerdict:
    """Cascata estatico -> juiz fraco -> juizes fortes -> caso de eval.

    Fail-closed em toda borda: excecao de juiz/runner/guard, resposta ilegivel,
    caso ausente ou lock divergente bloqueiam a proposta. ``judge``, ``runner`` e
    ``guard`` sao injetaveis para teste; os defaults usam as lanes reais e os
    scripts reais de ``<home>``.
    """
    if getattr(assessment, "action", "") != "create":
        return GateVerdict(
            False,
            "static",
            f"criterio deterministico negou ({getattr(assessment, 'action', '')!r}): "
            f"{getattr(assessment, 'reason', '')}",
        )

    judge = judge or _default_judge

    try:
        cheap = _judge_verdict(judge, CHEAP_JUDGE_TASK, fact)
    except Exception as exc:  # transporte caiu: fail-closed
        return GateVerdict(
            False, "cheap_judge", f"juiz fraco indisponivel: {type(exc).__name__}: {exc}"
        )
    if cheap != "promote":
        return GateVerdict(
            False, "cheap_judge", f"juiz fraco nao promove (veredito={cheap or 'ilegivel'!r})"
        )

    strong: Dict[str, str] = {}
    for task in STRONG_JUDGE_TASKS:
        try:
            strong[task] = _judge_verdict(judge, task, fact)
        except Exception as exc:
            return GateVerdict(
                False,
                "strong_judges",
                f"juiz forte {task!r} indisponivel: {type(exc).__name__}: {exc}",
                {"judges": strong},
            )
    if set(strong.values()) != {"promote"}:
        return GateVerdict(
            False,
            "strong_judges",
            f"juizes fortes nao concordam em promover: {strong}",
            {"judges": strong},
        )

    rb_path = red_before_path(key, home)
    lineage: Dict[str, Any] = {"judges": strong, "red_before": str(rb_path)}
    if not rb_path.exists():
        return GateVerdict(
            False,
            "eval_case",
            f"sem caso de eval vermelho-antes registrado ({rb_path}): "
            "nenhuma regra sobe por contador cru",
            lineage,
        )
    try:
        rb = json.loads(rb_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return GateVerdict(False, "eval_case", f"red_before ilegivel: {exc}", lineage)
    if not isinstance(rb, dict):
        return GateVerdict(False, "eval_case", "red_before nao e um objeto JSON", lineage)

    case_id = str(rb.get("case_id") or "").strip()
    before_exit = rb.get("exit_code")
    if not case_id:
        return GateVerdict(False, "eval_case", "red_before sem case_id", lineage)
    if not isinstance(before_exit, int) or isinstance(before_exit, bool) or before_exit == 0:
        return GateVerdict(
            False,
            "eval_case",
            f"red_before nao prova vermelho-antes (exit_code={before_exit!r})",
            lineage,
        )
    lineage.update({"case_id": case_id, "before_exit": before_exit})

    guard = guard or _default_guard
    runner = runner or _default_runner
    try:
        if guard() != 0:
            return GateVerdict(
                False, "eval_case", "placar nao confere com o lock (guard exit != 0)", lineage
            )
    except Exception as exc:
        return GateVerdict(
            False,
            "eval_case",
            f"guard do placar indisponivel: {type(exc).__name__}: {exc}",
            lineage,
        )

    try:
        after_exit = runner(case_id)
    except Exception as exc:
        return GateVerdict(
            False,
            "eval_case",
            f"runner do placar indisponivel para {case_id!r}: {type(exc).__name__}: {exc}",
            lineage,
        )
    lineage["after_exit"] = after_exit
    if after_exit != 0:
        return GateVerdict(
            False,
            "eval_case",
            f"caso {case_id!r} nao ficou verde-depois (runner exit={after_exit})",
            lineage,
        )

    return GateVerdict(
        True,
        "eval_case",
        f"cascata verde: caso {case_id!r} verde-depois (vermelho-antes exit={before_exit})",
        lineage,
    )