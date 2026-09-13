"""Context Utilization Evals & Evolution Integration.

Avalia o uso efetivo de itens de contexto pelo modelo e gera propostas de evolução/otimização
para o EvolutionLedger quando a taxa de aproveitamento está abaixo do esperado.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class ContextUtilizationMetrics:
    total_context_tokens: int
    utilized_tokens: int
    utilization_ratio: float  # utilized / total
    cited_items_count: int
    total_items_count: int
    stable_prefix_tokens: int
    cache_hit_efficiency: float
    recommendations: List[str] = field(default_factory=list)
    cited_item_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ContextUtilizationEvaluator:
    """Avaliador de utilização de contexto por modelos/agentes."""

    def __init__(self, low_utilization_threshold: float = 0.25):
        self.low_utilization_threshold = low_utilization_threshold

    def _extract_search_corpus(self, tool_calls: List[Dict[str, Any]], final_response: str) -> str:
        """Extrai todo texto produzido e argumentos de ferramentas para busca de citações."""
        corpus_parts: List[str] = [final_response or ""]
        for call in tool_calls or []:
            if isinstance(call, dict):
                # Extrai function name e argumentos
                fn = call.get("function")
                if isinstance(fn, dict):
                    corpus_parts.append(str(fn.get("name", "")))
                    corpus_parts.append(str(fn.get("arguments", "")))
                elif "name" in call:
                    corpus_parts.append(str(call.get("name", "")))
                    corpus_parts.append(str(call.get("arguments", "")))
                # Adiciona qualquer outro campo de string
                for k, v in call.items():
                    if k not in ("function", "name", "arguments"):
                        corpus_parts.append(str(v))
            else:
                corpus_parts.append(str(call))
        return "\n".join(corpus_parts).lower()

    def _extract_item_tokens_and_terms(self, item: Any) -> Set[str]:
        """Extrai termos-chave, IDs e títulos para match."""
        terms = set()
        item_id = getattr(item, "id", None)
        if item_id:
            terms.add(str(item_id).lower().strip())

        title = getattr(item, "title", None)
        if title:
            clean_title = str(title).lower().strip()
            if clean_title:
                terms.add(clean_title)
                # Extrai palavras significativas do título (tamanho >= 4)
                words = re.findall(r"[a-zA-Z0-9_\-\.]{4,}", clean_title)
                for w in words:
                    terms.add(w)

        metadata = getattr(item, "metadata", {}) or {}
        if isinstance(metadata, dict):
            symbol = metadata.get("symbol") or metadata.get("symbol_name") or metadata.get("name")
            if symbol:
                terms.add(str(symbol).lower().strip())
            key_terms = metadata.get("key_terms")
            if isinstance(key_terms, (list, set, tuple)):
                for kt in key_terms:
                    if kt:
                        terms.add(str(kt).lower().strip())

        return {t for t in terms if t}

    def _is_item_utilized(self, item: Any, corpus_lower: str) -> bool:
        """Verifica se um ContextItem foi referenciado no corpus por id, title, symbol name ou key terms."""
        terms = self._extract_item_tokens_and_terms(item)
        if not terms:
            return False

        for term in terms:
            # Match exato de substring para termos significativos
            if len(term) >= 3 and term in corpus_lower:
                return True
        return False

    def evaluate_usage(
        self,
        package: Any,
        tool_calls: List[Dict[str, Any]],
        final_response: str,
    ) -> ContextUtilizationMetrics:
        """Avalia quais ContextItems foram utilizados e calcula as métricas.

        Verifica quais ContextItems em `package.sections` foram referenciados
        em `final_response` ou `tool_calls` (por item id, title, symbol name, ou key terms).
        """
        corpus_lower = self._extract_search_corpus(tool_calls, final_response)

        all_items: List[Any] = []
        sections = getattr(package, "sections", {}) or {}
        if isinstance(sections, dict):
            for sec_items in sections.values():
                if isinstance(sec_items, list):
                    all_items.extend(sec_items)

        total_items_count = len(all_items)
        cited_items: List[Any] = []
        cited_item_ids: List[str] = []

        total_tokens = 0
        utilized_tokens = 0

        # Computa tokens totais e utilizados
        for item in all_items:
            token_cost = getattr(item, "token_cost", 0) or 0
            if not token_cost:
                content = getattr(item, "content", "") or ""
                token_cost = max(1, len(content) // 4)
            total_tokens += token_cost

            if self._is_item_utilized(item, corpus_lower):
                cited_items.append(item)
                item_id = getattr(item, "id", None) or f"item_{len(cited_items)}"
                cited_item_ids.append(str(item_id))
                utilized_tokens += token_cost

        # Se o package tiver total_tokens computado, considerar como base se for maior
        pkg_total_tokens = getattr(package, "total_tokens", 0)
        if pkg_total_tokens and pkg_total_tokens > total_tokens:
            total_tokens = pkg_total_tokens

        total_context_tokens = total_tokens
        cited_items_count = len(cited_items)
        utilization_ratio = (
            (utilized_tokens / total_context_tokens) if total_context_tokens > 0 else 1.0
        )

        # Stable prefix tokens calculation
        stable_prefix_tokens = 0
        stable_sections = ["identity", "task", "decisions"]
        if isinstance(sections, dict):
            for sec_name in stable_sections:
                for item in sections.get(sec_name, []):
                    cost = getattr(item, "token_cost", 0) or max(1, len(getattr(item, "content", "") or "") // 4)
                    stable_prefix_tokens += cost

        # Cache hit efficiency: proporção de tokens que residem no prefixo estável
        cache_hit_efficiency = (
            (stable_prefix_tokens / total_context_tokens) if total_context_tokens > 0 else 0.0
        )
        if cache_hit_efficiency > 1.0:
            cache_hit_efficiency = 1.0

        recommendations: List[str] = []
        if utilization_ratio < self.low_utilization_threshold:
            posture_id = getattr(package, "posture_id", "default")
            # Identifica se seções específicas estão inchadas sem citação
            uncited_sections: Dict[str, int] = {}
            if isinstance(sections, dict):
                for sec_name, s_items in sections.items():
                    uncited = [it for it in s_items if it not in cited_items]
                    if uncited:
                        uncited_sections[sec_name] = len(uncited)

            main_bloat = max(uncited_sections.items(), key=lambda x: x[1])[0] if uncited_sections else "context"
            recommendations.append(
                f"posture '{posture_id}' receiving too many irrelevant {main_bloat} items "
                f"(utilization ratio {utilization_ratio:.2%} < {self.low_utilization_threshold:.2%})"
            )
            recommendations.append(
                f"Consider tightening context policy or lowering token budget for {main_bloat} section."
            )

        return ContextUtilizationMetrics(
            total_context_tokens=total_context_tokens,
            utilized_tokens=utilized_tokens,
            utilization_ratio=round(utilization_ratio, 4),
            cited_items_count=cited_items_count,
            total_items_count=total_items_count,
            stable_prefix_tokens=stable_prefix_tokens,
            cache_hit_efficiency=round(cache_hit_efficiency, 4),
            recommendations=recommendations,
            cited_item_ids=cited_item_ids,
        )

    def record_to_ledger(
        self,
        metrics: ContextUtilizationMetrics,
        task_id: str,
        posture_id: str,
        ledger: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Registra a proposta de otimização no EvolutionLedger se aplicável."""
        proposal: Dict[str, Any] = {
            "proposal_id": f"context_opt_{task_id}_{posture_id}",
            "type": "context_utilization_optimization",
            "task_id": task_id,
            "posture_id": posture_id,
            "metrics": metrics.to_dict(),
            "recommendations": metrics.recommendations,
            "action": "reduce_budget" if metrics.utilization_ratio < self.low_utilization_threshold else "maintain",
            "rationale": (
                "; ".join(metrics.recommendations)
                if metrics.recommendations
                else f"Context utilization healthy at {metrics.utilization_ratio:.2%}"
            ),
        }

        if ledger is not None and hasattr(ledger, "submit"):
            submitted_id = ledger.submit(proposal)
            proposal["proposal_id"] = submitted_id

        return proposal
