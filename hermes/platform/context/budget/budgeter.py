"""ContextBudgeter e ArtifactStore — Gerenciamento estrito de orçamento de tokens.

Invariantes:
1. Seções protegidas (System/Identity, TaskSpec, Core Decisions) NUNCA são descartadas.
2. Progressive disclosure: se o orçamento apertar, degrada para 'summary' ou 'abstract'
   antes de descartar o item completamente.
3. Elisão de saídas grandes (Artifact Pointers): saídas de ferramentas ou artefatos > threshold
   são arquivados no ArtifactStore e substituídos por ponteiros leves no contexto.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from hermes.platform.context.policies.policy import ContextPolicy
from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.primitives.package import ContextManifest, ContextPackage


@dataclass
class StoredArtifact:
    """Artefato completo persistido no ArtifactStore."""
    uri: str
    artifact_type: str
    title: str
    content: str
    token_cost: int
    summary: str
    abstract: str
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


class ArtifactStore:
    """Armazenamento durável de artefatos grandes com elisão para ContextItem pointers."""
    
    def __init__(self, token_threshold: int = 1500):
        self.token_threshold = token_threshold
        self._store: Dict[str, StoredArtifact] = {}

    def store_or_pass(
        self,
        item_id: str,
        item_type: str,
        content: str,
        title: str = "",
        summary: Optional[str] = None,
        abstract: Optional[str] = None,
        trust: TrustLevel = TrustLevel.TRUSTED_INTERNAL_ARTIFACT,
    ) -> ContextItem:
        """Se o conteúdo exceder token_threshold, armazena no store e retorna ContextItem com pointer."""
        token_cost = max(1, len(content) // 4)
        if token_cost <= self.token_threshold:
            return ContextItem(
                id=item_id,
                item_type=item_type,
                source_uri=f"artifact://inline/{item_id}",
                content=content,
                title=title or item_id,
                summary=summary or (content[:300] + "..." if len(content) > 300 else content),
                abstract=abstract or (content[:100] + "..." if len(content) > 100 else content),
                trust=trust,
                token_cost=token_cost,
            )

        # Elisão de artefato grande: armazena full e cria pointer
        uri = f"artifact://store/{item_id}"
        auto_summary = summary or f"Large artifact ({token_cost} tokens) of type '{item_type}':\n" + content[:600] + "\n..."
        auto_abstract = abstract or f"Artifact pointer {item_id} ({token_cost} tokens)"

        self._store[uri] = StoredArtifact(
            uri=uri,
            artifact_type=item_type,
            title=title or item_id,
            content=content,
            token_cost=token_cost,
            summary=auto_summary,
            abstract=auto_abstract,
        )

        pointer_content = (
            f"[ARTIFACT_POINTER uri='{uri}' type='{item_type}' tokens='{token_cost}']\n"
            f"Summary: {auto_summary}\n"
            f"Use artifact read tool to load specific sections on demand."
        )

        return ContextItem(
            id=item_id,
            item_type="artifact_pointer",
            source_uri=uri,
            content=pointer_content,
            title=title or item_id,
            summary=auto_summary,
            abstract=auto_abstract,
            trust=trust,
            authority=AuthorityLevel.ADVISORY,
            token_cost=max(1, len(pointer_content) // 4),
            metadata={"full_uri": uri, "full_token_cost": token_cost},
        )

    def get_artifact(self, uri: str) -> Optional[StoredArtifact]:
        return self._store.get(uri)


class ContextBudgeter:
    """Orquestrador de orçamento, priorização por score e progressive disclosure."""

    def __init__(self, policy: ContextPolicy, total_budget: Optional[int] = None):
        self.policy = policy
        self.total_budget = total_budget or policy.max_tokens

    def fit_package(
        self,
        task_id: str,
        task_revision: int,
        candidates_by_section: Dict[str, List[ContextItem]],
        task_signals: Optional[List[str]] = None,
    ) -> ContextPackage:
        """Aplica a política e o orçamento aos candidatos, gerando ContextPackage e ContextManifest."""
        package_id = f"CP-{task_id[:8]}-{task_revision}-{int(time.time())}"
        manifest = ContextManifest(
            package_id=package_id,
            task_id=task_id,
            token_budget_limit=self.total_budget,
        )

        # Seções protegidas da política (ex: identity, task)
        protected_sections = set(self.policy.required_sections)
        selected_sections: Dict[str, List[ContextItem]] = {}
        representations: Dict[str, str] = {}

        current_tokens = 0
        total_candidates_count = 0

        # 1. Filtra por política de postura (allowed / forbidden)
        filtered_candidates: Dict[str, List[ContextItem]] = {}
        for sec, items in candidates_by_section.items():
            filtered_candidates[sec] = []
            for item in items:
                total_candidates_count += 1
                allowed, reason = self.policy.is_item_allowed(item)
                if not allowed:
                    manifest.record_exclusion(
                        item=item,
                        reason=f"Policy rejection: {reason}",
                        score=0.0,
                    )
                else:
                    filtered_candidates[sec].append(item)

        manifest.total_candidates = total_candidates_count

        # 2. Passada 1: Itens protegidos (Identity, TaskSpec, etc.)
        for sec in sorted(filtered_candidates.keys()):
            is_protected = sec in protected_sections
            items = filtered_candidates[sec]
            if is_protected:
                selected_sections[sec] = []
                for item in items:
                    rep = "summary" if self.policy.prefer_summaries else "full"
                    cost = max(1, len(item.get_representation(rep)) // 4)
                    if current_tokens + cost > self.total_budget:
                        manifest.record_exclusion(
                            item=item,
                            reason=f"Protected section token budget exceeded: required {cost} tokens, remaining {self.total_budget - current_tokens}",
                            score=1.0,
                        )
                        raise ValueError(
                            f"Protected section '{sec}' exceeds total token budget "
                            f"({current_tokens + cost} > {self.total_budget})"
                        )
                    selected_sections[sec].append(item)
                    representations[item.id] = rep
                    current_tokens += cost
                    manifest.record_inclusion(
                        item=item,
                        reason=f"Protected section '{sec}'",
                        score=1.0,
                        level=rep,
                        tokens=cost,
                    )

        # 3. Passada 2: Itens não-protegidos ordenados por Context Value Density (Score)
        scored_pool: List[Tuple[float, str, ContextItem]] = []
        signals = task_signals or []

        for sec, items in filtered_candidates.items():
            if sec in protected_sections:
                continue
            for item in items:
                # Afinidade com sinais da tarefa
                affinity = 1.0
                for sig in signals:
                    if sig.lower() in item.content.lower() or sig.lower() in item.title.lower():
                        affinity += 0.5

                score = item.calculate_score(
                    task_affinity=affinity,
                    posture_affinity=1.0,
                    source_quality=1.0,
                )
                scored_pool.append((score, sec, item))

        # Ordenação decrescente por score
        scored_pool.sort(key=lambda x: x[0], reverse=True)

        # 4. Alocação Greedy com Progressive Disclosure
        for score, sec, item in scored_pool:
            full_cost = max(1, len(item.get_representation("full")) // 4)
            summary_cost = max(1, len(item.get_representation("summary")) // 4)
            abstract_cost = max(1, len(item.get_representation("abstract")) // 4)

            chosen_level: Optional[str] = None
            chosen_cost = 0

            if not self.policy.prefer_summaries and current_tokens + full_cost <= self.total_budget:
                chosen_level = "full"
                chosen_cost = full_cost
            elif current_tokens + summary_cost <= self.total_budget:
                chosen_level = "summary"
                chosen_cost = summary_cost
            elif current_tokens + abstract_cost <= self.total_budget:
                chosen_level = "abstract"
                chosen_cost = abstract_cost

            if chosen_level is not None:
                if sec not in selected_sections:
                    selected_sections[sec] = []
                selected_sections[sec].append(item)
                representations[item.id] = chosen_level
                current_tokens += chosen_cost
                manifest.record_inclusion(
                    item=item,
                    reason=f"Included via score ({score:.3f}) as {chosen_level}",
                    score=score,
                    level=chosen_level,
                    tokens=chosen_cost,
                )
            else:
                manifest.record_exclusion(
                    item=item,
                    reason=f"Token budget exceeded (needs at least {abstract_cost} tokens)",
                    score=score,
                )

        manifest.total_tokens_used = current_tokens

        package = ContextPackage(
            id=package_id,
            task_id=task_id,
            task_revision=task_revision,
            posture_id=self.policy.posture_name,
            budget_limit=self.total_budget,
            sections=selected_sections,
            representations=representations,
            manifest=manifest,
        )
        return package
