"""MemoryRouter — Roteamento determinístico e semântico de fatos para destinos de memória.

Destinos suportados:
- "working": Estado temporário / execução em andamento (Scratchpad / Working Memory)
- "task": Desfecho de tarefa, resultados, riscos residuais
- "obsidian": Decisões arquiteturais, convenções canônicas de projeto, ADRs
- "skill": Procedimentos operacionais padrão, fluxos reutilizáveis, receitas
- "core_user": Preferências do usuário, identidade, idioma, timezone
- "core_agent": Postura, diretrizes operacionais do agente, restrições comportamentais
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from hermes.platform.context.memory.candidate import DestinationType, MemoryCandidate, ScopeType
from hermes.platform.context.memory.consolidation import MemoryConsolidator
from hermes.platform.context.memory.staging import MemoryStagingStore

# Razões de staging persistidas no MemoryStagingStore (gate auditável).
STAGE_REASON_LOW_CONFIDENCE = "below_confidence_threshold"
STAGE_REASON_CONFLICT = "conflict_with_canonical"

# O limiar de consolidação direta vive em MemoryCandidate.is_high_confidence()
# (>= 0.85, candidate.py) — o router só o aplica via process_candidate; o reforço
# por recorrência entre sessões (InstinctStore no dream) eleva o candidato até ele.


class MemoryRouter:
    """Roteador de fatos para candidatos a memória com classificação baseada em regras."""

    # Regras por Regex compilados para categorização de baixo custo (zero LLM overhead)
    RE_CORE_USER = re.compile(
        r"\b(user\s+(prefers?|likes?|wants?|is|name|timezone|language|style)|my\s+name|i\s+prefer|i\s+like|user_pref|call\s+me|prefer\s+to\s+be\s+addressed)\b",
        re.IGNORECASE,
    )
    RE_CORE_AGENT = re.compile(
        r"\b(agent\s+(posture|guideline|behavior|persona|role|constraint|must|shall|should\s+never)|operating\s+guideline|agent_policy|agent_posture)\b",
        re.IGNORECASE,
    )
    RE_SKILL = re.compile(
        r"\b(workflow|procedure|how\s+to|step[-\s]by[-\s]step|recipe|playbook|reusable|guidelines\s+for\s+running|command\s+sequence|pipeline\s+step)\b",
        re.IGNORECASE,
    )
    RE_OBSIDIAN = re.compile(
        r"\b(adr[-\s]?\d*|architecture|decision|convention|standard|canonical|design\s+pattern|system\s+boundary|architectural|rfc\b)\b",
        re.IGNORECASE,
    )
    RE_TASK = re.compile(
        r"\b(task\s+(outcome|result|completed|failed|status)|residual\s+risk|deliverable|pr\s+merged|test\s+suite\s+passed|completed\s+with|post[-\s]?mortem)\b",
        re.IGNORECASE,
    )
    RE_WORKING = re.compile(
        r"\b(temporary|scratchpad|in[-\s]progress|current\s+turn|turn\s+state|execution\s+state|transient|buffer|pending\s+verification|running\s+step)\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        consolidator: Optional[MemoryConsolidator] = None,
        staging_store: Optional[MemoryStagingStore] = None,
    ):
        self.consolidator = consolidator or MemoryConsolidator()
        # Degrau de staging (P11): onde o candidato de baixa confiança espera
        # persistido até ser promovido. Passe staging_store=None para não persistir
        # (o candidato ainda vira "pending", mas nada é gravado).
        self.staging_store = staging_store if staging_store is not None else MemoryStagingStore()

    def classify_destination(self, fact: str) -> DestinationType:
        """Classifica o fato segundo as regras de negócio."""
        # 1. User Preference / Identity -> core_user
        if self.RE_CORE_USER.search(fact):
            return "core_user"

        # 2. Agent Posture / Guideline -> core_agent
        if self.RE_CORE_AGENT.search(fact):
            return "core_agent"

        # 3. Reusable workflow / procedure -> skill
        if self.RE_SKILL.search(fact):
            return "skill"

        # 4. Architecture decision / convention -> obsidian
        if self.RE_OBSIDIAN.search(fact):
            return "obsidian"

        # 5. Task outcome / residual risks -> task
        if self.RE_TASK.search(fact):
            return "task"

        # 6. Temporary / execution state -> working
        if self.RE_WORKING.search(fact):
            return "working"

        # Fallback padrão: facts genéricos começam em working memory se não categorizados
        return "working"

    def route_fact(
        self,
        fact: str,
        source_uri: str,
        confidence: float = 0.9,
        scope: ScopeType = "project",
    ) -> MemoryCandidate:
        """Roteia o fato construindo um MemoryCandidate com o proposed_destination correto."""
        destination = self.classify_destination(fact)
        return MemoryCandidate(
            fact=fact.strip(),
            source_uri=source_uri.strip(),
            confidence=confidence,
            scope=scope,
            proposed_destination=destination,
            provenance=[source_uri.strip()] if source_uri else [],
            status="pending",
        )

    def stage_candidate(self, candidate: MemoryCandidate, reason: str = "") -> bool:
        """Degrau de staging: o candidato espera PERSISTIDO como ``pending``.

        O modelo de dados já previa ``status="pending"``; sem este degrau o roteador
        descartava (``rejected``) candidatos abaixo do limiar de
        ``MemoryCandidate.is_high_confidence()``. Nada é descartado: o candidato fica
        no store com proveniência e confiança até ser promovido (o reforço por
        recorrência entre sessões eleva a confiança até o limiar). Retorna True se
        ficou persistido.
        """
        candidate.status = "pending"
        if self.staging_store is None:
            return False
        self.staging_store.stage_candidate(candidate, reason=reason)
        return True

    def process_candidate(self, candidate: MemoryCandidate, memory_provider: Any) -> bool:
        """Processa o candidato aplicando verificações de conflito e consolidação no memory_provider.

        Contrato:
        - confiança abaixo do limiar de ``MemoryCandidate.is_high_confidence()`` (0.85):
          o candidato NÃO é descartado — vira ``status="pending"`` e é PERSISTIDO no
          staging store (degrau de staging, P11). Retorna False (não consolidou).
        - confiança alta mas conflito com item existente: ``status="rejected"`` e False;
          o chamador decide o que fazer (no dream, o candidato volta para o staging).
        - confiança alta, sem conflito: consolida no memory_provider e retorna True.
        """
        # Degrau de staging: candidato abaixo do limiar espera em vez de ser jogado fora.
        # A promoção vem de fora: recorrência entre sessões (InstinctStore no dream)
        # eleva a confiança até este limiar. Retorno False = não consolidou (o chamador
        # discrimina pelo status do candidato: "pending" = estagiado).
        if not candidate.is_high_confidence():
            self.stage_candidate(candidate, reason=STAGE_REASON_LOW_CONFIDENCE)
            return False

        # Verifica conflitos com itens existentes se o provedor expuser método para consulta
        existing_items: List[Any] = []
        if hasattr(memory_provider, "get_existing_facts"):
            existing_items = memory_provider.get_existing_facts(candidate.proposed_destination)
        elif hasattr(memory_provider, "list_decisions") and candidate.proposed_destination == "obsidian":
            existing_items = memory_provider.list_decisions()
        elif hasattr(memory_provider, "get_facts"):
            existing_items = memory_provider.get_facts(candidate.proposed_destination)
        elif isinstance(memory_provider, dict) and candidate.proposed_destination in memory_provider:
            existing_items = memory_provider[candidate.proposed_destination]

        conflict = self.consolidator.detect_conflicts(candidate, existing_items)
        if conflict:
            candidate.status = "rejected"
            return False

        # Aplica consolidação
        return self.consolidator.consolidate(candidate, memory_provider)
