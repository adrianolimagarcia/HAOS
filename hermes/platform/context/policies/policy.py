"""ContextPolicy — Políticas de inclusão, exclusão e isolamento de contexto por postura.

Garante isolamento entre agentes (ex.: Reviewer não recebe o chain of thought do Coder,
Architect recebe decisões e grafos em vez de 15 arquivos de código bruto).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from hermes.platform.context.primitives.item import ContextItem, TrustLevel


@dataclass
class SectionBudgetRatio:
    """Proporção de alocação de tokens para cada seção."""
    identity: float = 0.05
    task: float = 0.10
    decisions: float = 0.15
    code: float = 0.25
    memory: float = 0.10
    artifacts: float = 0.15
    retrieved: float = 0.10
    working_reserve: float = 0.10


@dataclass
class ContextPolicy:
    """Política de montagem de contexto adaptada à postura do agente."""
    posture_name: str
    allowed_types: Set[str] = field(default_factory=set)
    forbidden_types: Set[str] = field(default_factory=set)
    required_sections: Set[str] = field(default_factory=lambda: {"identity", "task"})
    section_ratios: SectionBudgetRatio = field(default_factory=SectionBudgetRatio)
    min_trust_level: TrustLevel = TrustLevel.EXTERNAL_UNTRUSTED
    allow_external_untrusted: bool = True
    prefer_summaries: bool = False
    max_tokens: int = 64000

    def is_item_allowed(self, item: ContextItem) -> tuple[bool, str]:
        """Avalia se um ContextItem é permitido sob esta política."""
        # Checagem de tipos expressamente proibidos
        if item.item_type in self.forbidden_types:
            return False, f"item_type '{item.item_type}' forbidden for posture '{self.posture_name}'"

        # Checagem de itens externos não confiáveis
        if item.trust == TrustLevel.EXTERNAL_UNTRUSTED and not self.allow_external_untrusted:
            return False, f"external untrusted content not allowed for posture '{self.posture_name}'"

        # Checagem de limiar mínimo de confiança
        from hermes.platform.context.primitives.item import TRUST_ORDER
        try:
            item_trust_idx = TRUST_ORDER.index(item.trust)
            min_trust_idx = TRUST_ORDER.index(self.min_trust_level)
            if item_trust_idx > min_trust_idx:
                return False, f"item trust '{item.trust.value}' lower than min_trust_level '{self.min_trust_level.value}'"
        except ValueError:
            pass

        # Checagem de whitelist se configurada
        if self.allowed_types and item.item_type not in self.allowed_types:
            return False, f"item_type '{item.item_type}' not in allowed list for posture '{self.posture_name}'"

        return True, "allowed"


# Políticas canônicas pré-definidas por postura

def get_architect_policy(max_tokens: int = 64000) -> ContextPolicy:
    """Postura Architect: Foco em decisões, ADRs, grafos de dependência e requisitos."""
    return ContextPolicy(
        posture_name="architect",
        allowed_types={
            "identity", "task_spec", "architecture_decision", "project_doc",
            "dependency_graph", "research_findings", "system_prompt",
            "artifact_pointer", "interface_definition", "blocker"
        },
        forbidden_types={
            "raw_terminal_log", "coder_chain_of_thought", "temporary_working_diff"
        },
        section_ratios=SectionBudgetRatio(
            identity=0.08,
            task=0.12,
            decisions=0.30,
            code=0.10,
            memory=0.15,
            artifacts=0.10,
            retrieved=0.10,
            working_reserve=0.05,
        ),
        prefer_summaries=False,
        max_tokens=max_tokens,
    )


def get_coder_policy(max_tokens: int = 64000) -> ContextPolicy:
    """Postura Coder: Foco em TaskSpec, símbolos LSP, definições, testes e diffs locais."""
    return ContextPolicy(
        posture_name="coder",
        allowed_types={
            "identity", "task_spec", "architecture_decision", "code_symbol",
            "file_content", "lsp_diagnostics", "test_failure", "git_diff",
            "implementation_artifact", "system_prompt", "artifact_pointer"
        },
        forbidden_types={
            "holistic_community_report", "irrelevant_marketing_docs"
        },
        section_ratios=SectionBudgetRatio(
            identity=0.05,
            task=0.10,
            decisions=0.15,
            code=0.35,
            memory=0.05,
            artifacts=0.15,
            retrieved=0.05,
            working_reserve=0.10,
        ),
        prefer_summaries=False,
        max_tokens=max_tokens,
    )


def get_reviewer_policy(max_tokens: int = 64000) -> ContextPolicy:
    """Postura Reviewer: Clean Review Context.
    
    Recebe TaskSpec, Critérios de Aceite, Diff e Testes.
    ISOLAMENTO ESTRITO: Baniu terminantemente o 'coder_chain_of_thought' e chats intermediários!
    """
    return ContextPolicy(
        posture_name="reviewer",
        allowed_types={
            "identity", "task_spec", "acceptance_criteria", "architecture_decision",
            "git_diff", "test_report", "validation_result", "system_prompt",
            "artifact_pointer"
        },
        forbidden_types={
            "coder_chain_of_thought",
            "intermediate_agent_chat",
            "temporary_scratchpad",
            "unverified_coder_opinion"
        },
        section_ratios=SectionBudgetRatio(
            identity=0.05,
            task=0.20,
            decisions=0.20,
            code=0.25,
            memory=0.05,
            artifacts=0.20,
            retrieved=0.00,
            working_reserve=0.05,
        ),
        prefer_summaries=False,
        max_tokens=max_tokens,
    )


def get_researcher_policy(max_tokens: int = 64000) -> ContextPolicy:
    """Postura Researcher: Foco em evidências, web research, documentação e hipóteses."""
    return ContextPolicy(
        posture_name="researcher",
        allowed_types={
            "identity", "task_spec", "research_question", "web_search_result",
            "document_content", "external_evidence", "system_prompt",
            "architecture_decision", "memory_finding"
        },
        forbidden_types={
            "raw_terminal_log", "unrelated_code_diff"
        },
        section_ratios=SectionBudgetRatio(
            identity=0.05,
            task=0.10,
            decisions=0.10,
            code=0.05,
            memory=0.15,
            artifacts=0.15,
            retrieved=0.35,
            working_reserve=0.05,
        ),
        allow_external_untrusted=True,
        prefer_summaries=True,
        max_tokens=max_tokens,
    )


def get_security_policy(max_tokens: int = 64000) -> ContextPolicy:
    """Postura Security: Foco em superfície de ataque, diffs, dependências e auth flows."""
    return ContextPolicy(
        posture_name="security",
        allowed_types={
            "identity", "task_spec", "git_diff", "threat_model", "auth_flow",
            "dependency_manifest", "security_decision", "system_prompt",
            "validation_result"
        },
        forbidden_types={
            "coder_chain_of_thought", "marketing_doc", "conversational_filler"
        },
        section_ratios=SectionBudgetRatio(
            identity=0.05,
            task=0.15,
            decisions=0.25,
            code=0.30,
            memory=0.10,
            artifacts=0.10,
            retrieved=0.00,
            working_reserve=0.05,
        ),
        allow_external_untrusted=False,
        max_tokens=max_tokens,
    )


POLICY_REGISTRY: Dict[str, callable] = {
    "architect": get_architect_policy,
    "coder": get_coder_policy,
    "reviewer": get_reviewer_policy,
    "researcher": get_researcher_policy,
    "security": get_security_policy,
}


def resolve_context_policy(posture_name: str, max_tokens: int = 64000) -> ContextPolicy:
    """Resolve a ContextPolicy para a postura informada ou retorna padrão equilibrado."""
    factory = POLICY_REGISTRY.get(posture_name.lower())
    if factory:
        return factory(max_tokens=max_tokens)
    # Default: coder policy adaptada
    policy = get_coder_policy(max_tokens=max_tokens)
    policy.posture_name = posture_name
    return policy
