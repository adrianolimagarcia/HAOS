"""Context Engine — seleção por relevância e orçamento (A3, Emenda 30).

Puras e determinísticas: nenhum LLM aqui (o runtime decide o que é estado/
política). ``rank_sections`` pontua cada seção por (peso do trust level ×
cobertura de sinais da task); ``fit_budget`` escolhe greedy por score mas NUNCA
droga seções protegidas (trust >= canonical_obsidian). Retorna sempre cópias
das listas de chaves — o pacote de entrada não é mutado.
"""

import json
from typing import Dict, List, Optional, Tuple

from hermes.platform.memory.context_engine.builder import ContextPackage

# Peso por trust level (decai com a confiabilidade; ~0.2 p/ externo não
# confiável porque é "dado apenas", nunca instrução).
TRUST_WEIGHT = {
    "core_policy": 1.0,
    "system": 0.95,
    "canonical_obsidian": 0.90,
    "internal": 0.80,
    "untrusted_external": 0.20,
}

# Seções que o seletor NUNCA droga (a constituição/overlay/intenção da task
# e o conhecimento canônico ficam sempre no contexto).
PROTECTED_TRUST = ("core_policy", "system", "canonical_obsidian")

_DEFAULT_WEIGHT = 0.5


def section_text(content: object) -> str:
    return json.dumps(content, default=str)


def _trust_weight(section) -> float:
    return TRUST_WEIGHT.get(section.trust_level, _DEFAULT_WEIGHT)


def _signal_hits(text: str, signals: List[str]) -> int:
    lowered = text.lower()
    return sum(1 for s in signals if s and s.lower() in lowered)


def relevance_score(
    section,
    task_signals: Optional[List[str]] = None,
) -> float:
    """Pontuação determinística: peso do trust × cobertura dos sinais.

    Sem sinais, tudo vale 1.0×peso (a ordenação fica por confiabilidade).
    Com sinais, cada termo distinto achado adiciona 0.1 até 5 termos (fator
    0.5..1.0 sobre o peso).
    """
    weight = _trust_weight(section)
    hits = _signal_hits(section_text(section.content), list(task_signals or []))
    coverage = min(1.0, 0.5 + 0.1 * hits)
    return weight * coverage


def rank_sections(
    package: ContextPackage,
    task_signals: Optional[List[str]] = None,
) -> List[Tuple[str, float]]:
    """(chave, score) ordenado desc por relevância (empates: ordem do dict)."""
    scored = [(key, relevance_score(section, task_signals))
              for key, section in package.sections.items()]
    return sorted(scored, key=lambda kv: (-kv[1], kv[0]))


def estimated_tokens(section) -> int:
    return max(1, len(section_text(section.content)) // 4)


def fit_budget(
    package: ContextPackage,
    budget_tokens: Optional[int] = None,
    task_signals: Optional[List[str]] = None,
) -> Tuple[List[str], List[str]]:
    """(mantidas, dropadas) greedy por score respeitando o orçamento.

    Seções protegidas (trust core_policy/system/canonical_obsidian) entram
    primeiro e nunca são dropadas, mesmo que estourem o orçamento. Sem
    ``budget_tokens`` usa o ``token_budget_limit`` do pacote."""
    budget = budget_tokens if budget_tokens is not None else package.token_budget_limit
    ranked = rank_sections(package, task_signals)
    kept: List[str] = []
    dropped: List[str] = []
    used = 0
    for key, _score in ranked:
        section = package.sections[key]
        cost = estimated_tokens(section)
        protected = section.trust_level in PROTECTED_TRUST
        if used + cost <= budget or protected:
            kept.append(key)
            used += cost
        else:
            dropped.append(key)
    return kept, dropped
