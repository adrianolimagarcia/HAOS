"""Context Engine — guard anti-poisoning e anti-anchoring (A3, Emenda 30).

Anti-poisoning: conteúdo ``untrusted_external`` é sempre tratado como DADO
(``{"DATA_ONLY": …}`` pelo builder). ``assess_poisoning`` é a heurística
determinística que sinaliza tentativas de injeção de instrução (frases de
override/desvio) ANTES de o conteúdo entrar no contexto — o runtime decide por
política; não é julgamento de LLM.

Anti-anchoring (reviewer): ``build_reviewer_package`` monta o pacote do
reviewer SEM o transcript do implementador (spec/AC, mudanças como linhas-de-
cisão, resultados de teste e critérios de review) — o reviewer julga contra o
contrato, nunca ancorado na narrativa de quem implementou.
"""

import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from hermes.platform.memory.context_engine.builder import (
    ContextPackage, SectionContent,
)
from hermes.platform.memory.context_engine.compression import (
    split_decision_lines,
)
from hermes.platform.tasks.spec import TaskSpec

# Frases/padrões típicos de injeção de instrução (heurística documentada).
_POISON_PATTERNS = (
    "ignore all previous",
    "ignore previous instructions",
    "ignore prior instructions",
    "disregard previous",
    "override your instructions",
    "override your system",
    "you are now",
    "act as system",
    "pretend you are the system",
    "forget your instructions",
)


def assess_poisoning(content: str) -> Tuple[bool, List[str]]:
    """(risco, razões) — True quando o texto contém tentativa de override.

    Puramente determinístico: normaliza para minúsculas e procura os padrões.
    Sem padrões -> (False, [])."""
    lowered = (content or "").lower()
    reasons = [p for p in _POISON_PATTERNS if p in lowered]
    return bool(reasons), reasons


def _digest(sections: Dict[str, SectionContent]) -> str:
    content_str = json.dumps({k: v.content for k, v in sections.items()},
                             default=str)
    return hashlib.sha256(content_str.encode()).hexdigest()


def _tokens(sections: Dict[str, SectionContent]) -> int:
    content_str = json.dumps({k: v.content for k, v in sections.items()},
                             default=str)
    return len(content_str) // 4


def build_reviewer_package(
    task: TaskSpec,
    *,
    posture_id: str = "reviewer",
    changes_summary: Optional[str] = None,
    test_results: Optional[Dict[str, Any]] = None,
    review_criteria: Optional[List[Dict[str, Any]]] = None,
    decision_markers: Optional[List[str]] = None,
    implementer_transcript: Optional[str] = None,
    token_budget_limit: int = 64000,
) -> ContextPackage:
    """Pacote de review independente (anti-anchoring).

    ``implementer_transcript`` é ACEITO na assinatura apenas para documentar o
    contrato: ele NUNCA entra nas seções — a ausência é o invariant. Mudanças
    entram via linhas-de-decisão (``changes_summary`` filtrado pelos
    marcadores), nunca como narrativa do implementador."""
    sections: Dict[str, SectionContent] = {}

    sections["system_constitution"] = SectionContent(
        trust_level="core_policy",
        content="HAOS review gate: verdict against the acceptance contract, "
                "independent of the implementer narrative.",
    )
    sections["task_intent"] = SectionContent(
        trust_level="system",
        content={"goal": task.goal, "description": task.description,
                 "acceptance": [a.__dict__ for a in task.acceptance_criteria]},
    )
    sections["review_criteria"] = SectionContent(
        trust_level="system",
        content=review_criteria or [],
    )
    if changes_summary:
        decisions = split_decision_lines(changes_summary, markers=decision_markers)
        sections["changes"] = SectionContent(
            trust_level="internal",
            content={"decisions": decisions},
        )
    if test_results:
        sections["test_results"] = SectionContent(
            trust_level="internal",
            content=test_results,
        )
    return ContextPackage(
        id=f"ctx-rv-{uuid.uuid4().hex[:8]}",
        task_id=task.id,
        task_revision=task.version,
        posture_id=posture_id,
        sections=sections,
        token_count=_tokens(sections),
        token_budget_limit=token_budget_limit,
        digest_hash=_digest(sections),
    )
