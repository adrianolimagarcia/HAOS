"""MemoryConsolidator — Deduplicação, detecção de conflitos e consolidação de candidatos.

Garante que fatos duplicados sejam agregados, que contradições/conflitos com itens existentes
sejam identificados, e que candidatos válidos sejam promovidos ao destino final.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional

from hermes.platform.context.memory.candidate import MemoryCandidate


class MemoryConsolidator:
    """Consolidador de candidatos a memória."""

    def __init__(self, similarity_threshold: float = 0.85):
        self.similarity_threshold = similarity_threshold

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normaliza texto para comparação lexical."""
        text = text.lower()
        text = re.sub(r"[^\w\s]", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def _are_lexically_similar(self, text_a: str, text_b: str) -> bool:
        """Calcula similaridade lexical entre dois textos normalizados."""
        norm_a = self._normalize_text(text_a)
        norm_b = self._normalize_text(text_b)
        if norm_a == norm_b:
            return True
        if not norm_a or not norm_b:
            return False
        ratio = difflib.SequenceMatcher(None, norm_a, norm_b).ratio()
        return ratio >= self.similarity_threshold

    def deduplicate_candidates(self, candidates: List[MemoryCandidate]) -> List[MemoryCandidate]:
        """Deduplica lista de candidatos com base em similaridade lexical / fato normalizado.
        
        Ao encontrar duplicatas, preserva o candidato com maior confiança ou mais recente,
        mesclando suas proveniências.
        """
        deduped: List[MemoryCandidate] = []

        for candidate in candidates:
            match_found = False
            for existing in deduped:
                # Compara escopo e destino se aplicável, ou similaridade lexical direta do fato
                if existing.proposed_destination == candidate.proposed_destination and self._are_lexically_similar(
                    existing.fact, candidate.fact
                ):
                    match_found = True
                    # Atualiza com maior confiança
                    if candidate.confidence > existing.confidence:
                        existing.confidence = candidate.confidence
                    # Mescla proveniências sem repetições mantendo ordem
                    for prov in candidate.provenance:
                        if prov not in existing.provenance:
                            existing.provenance.append(prov)
                    break

            if not match_found:
                deduped.append(candidate)

        return deduped

    def detect_conflicts(
        self, candidate: MemoryCandidate, existing_items: List[Any]
    ) -> Optional[Dict[str, Any]]:
        """Detecta conflitos ou contradições entre o candidato e itens existentes na memória.
        
        Verifica negações diretas, polaridades opostas ou sobreposições contraditórias.
        Retorna dicionário descrevendo o conflito se detectado, ou None.
        """
        cand_norm = self._normalize_text(candidate.fact)
        words_cand = set(cand_norm.split())

        # Palavras indicativas de negação / polaridade oposta
        negation_tokens = {"not", "never", "no", "cannot", "isnt", "arent", "dont", "doesnt", "nao", "nunca", "jamais"}

        for item in existing_items:
            # Extrai texto do item existente (pode ser dict, ContextItem, string ou objeto com content/fact)
            if isinstance(item, dict):
                fact_text = item.get("fact") or item.get("content") or item.get("summary") or ""
                item_id = item.get("id", "unknown")
            elif hasattr(item, "fact"):
                fact_text = getattr(item, "fact")
                item_id = getattr(item, "id", "unknown")
            elif hasattr(item, "content"):
                fact_text = getattr(item, "content")
                item_id = getattr(item, "id", "unknown")
            elif isinstance(item, str):
                fact_text = item
                item_id = "string_item"
            else:
                continue

            item_norm = self._normalize_text(fact_text)
            if not item_norm or item_norm == cand_norm:
                # Se for idêntico, é duplicata, não conflito direto
                continue

            words_item = set(item_norm.split())
            shared_words = words_cand.intersection(words_item)
            non_stop_shared = {w for w in shared_words if len(w) > 3}

            # Se compartilham termos chave substantivos mas têm inversão de negação
            cand_has_negation = bool(words_cand.intersection(negation_tokens))
            item_has_negation = bool(words_item.intersection(negation_tokens))

            if len(non_stop_shared) >= 2 and (cand_has_negation != item_has_negation):
                # Verificação de proximidade semântica dos termos principais
                # Exemplo: "router falls back to secondary" vs "router never falls back to secondary"
                return {
                    "candidate_id": candidate.id,
                    "conflicting_item_id": item_id,
                    "candidate_fact": candidate.fact,
                    "existing_fact": fact_text,
                    "reason": "Polarity mismatch on shared core concepts",
                    "shared_tokens": list(non_stop_shared),
                }

            # Outro caso de conflito: mesma chave/atributo com valores declarados diferentes
            # Ex: "theme is dark" vs "theme is light"
            if len(non_stop_shared) >= 2:
                # Calcula similaridade: se é muito similar mas não idêntico (> 0.70 e < 0.95)
                ratio = difflib.SequenceMatcher(None, cand_norm, item_norm).ratio()
                if 0.70 <= ratio < 0.98:
                    # Detecta divergência pontual (possível substituição / contradição)
                    diffs = [
                        w for w in words_cand.symmetric_difference(words_item)
                        if len(w) > 2 and w not in negation_tokens
                    ]
                    if diffs:
                        return {
                            "candidate_id": candidate.id,
                            "conflicting_item_id": item_id,
                            "candidate_fact": candidate.fact,
                            "existing_fact": fact_text,
                            "reason": "Divergent attributes on similar statement",
                            "differing_tokens": list(diffs),
                        }

        return None

    def consolidate(self, candidate: MemoryCandidate, destination_writer: Any) -> bool:
        """Aplica consolidação gravando o fato no destination_writer e atualizando o status do candidato."""
        if candidate.status == "rejected":
            return False

        try:
            success = False
            # Suporta diferentes interfaces de destination_writer
            if callable(destination_writer):
                res = destination_writer(candidate)
                success = bool(res) if res is not None else True
            elif hasattr(destination_writer, "write_candidate"):
                res = destination_writer.write_candidate(candidate)
                success = bool(res) if res is not None else True
            elif hasattr(destination_writer, "write_fact"):
                res = destination_writer.write_fact(candidate.fact, candidate.source_uri, candidate.proposed_destination)
                success = bool(res) if res is not None else True
            elif hasattr(destination_writer, "save_candidate"):
                res = destination_writer.save_candidate(candidate)
                success = bool(res) if res is not None else True
            elif hasattr(destination_writer, "append"):
                destination_writer.append(candidate)
                success = True
            else:
                # Se não tem método conhecido, tenta salvar em dict ou store genérico
                success = False

            if success:
                candidate.status = "consolidated"
                return True
            else:
                candidate.status = "rejected"
                return False
        except Exception:
            candidate.status = "rejected"
            return False
