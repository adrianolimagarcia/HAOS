"""ContextItem — Primitiva fundamental de informação selecionada para contexto.

Separação estrita entre Relevância, Confiabilidade (Trust) e Autoridade (Authority).
Toda informação recuperada possui proveniência, nível de confiança e custo em tokens.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class TrustLevel(str, enum.Enum):
    """Hierarquia de confiabilidade do contexto.
    
    Quanto mais baixo, mais o conteúdo é tratado estritamente como DADOS externos
    e NUNCA como instrução executável.
    """
    SYSTEM = "system"
    TEAM_POLICY = "team_policy"
    TASK_SPEC = "task_spec"
    PROJECT_INSTRUCTIONS = "project_instructions"
    ARCHITECTURE_DECISIONS = "architecture_decisions"
    TRUSTED_INTERNAL_ARTIFACT = "trusted_internal_artifact"
    MEMORY = "memory"
    AGENT_MESSAGES = "agent_messages"
    MCP_RESULTS = "mcp_results"
    DOCUMENT_CONTENT = "document_content"
    EXTERNAL_UNTRUSTED = "external_untrusted"


# Ordem de precedência de confiança (de maior para menor)
TRUST_ORDER: List[TrustLevel] = [
    TrustLevel.SYSTEM,
    TrustLevel.TEAM_POLICY,
    TrustLevel.TASK_SPEC,
    TrustLevel.PROJECT_INSTRUCTIONS,
    TrustLevel.ARCHITECTURE_DECISIONS,
    TrustLevel.TRUSTED_INTERNAL_ARTIFACT,
    TrustLevel.MEMORY,
    TrustLevel.AGENT_MESSAGES,
    TrustLevel.MCP_RESULTS,
    TrustLevel.DOCUMENT_CONTENT,
    TrustLevel.EXTERNAL_UNTRUSTED,
]


class AuthorityLevel(str, enum.Enum):
    """Nível de autoridade normativa sobre o comportamento do agente."""
    SYSTEM = "system"
    ARCHITECTURE = "architecture"
    TASK = "task"
    ADVISORY = "advisory"
    NONE = "none"


AUTHORITY_WEIGHTS: Dict[AuthorityLevel, float] = {
    AuthorityLevel.SYSTEM: 1.0,
    AuthorityLevel.ARCHITECTURE: 0.9,
    AuthorityLevel.TASK: 0.8,
    AuthorityLevel.ADVISORY: 0.4,
    AuthorityLevel.NONE: 0.1,
}


@dataclass
class ContextItem:
    """Unidade atômica de contexto selecionável."""
    id: str
    item_type: str  # task_spec, architecture_decision, code_symbol, test_diff, artifact_pointer, etc.
    source_uri: str  # lsp://..., obsidian://..., task://..., artifact://..., web://...
    content: str
    title: str = ""
    summary: str = ""
    abstract: str = ""
    provenance: str = "canonical"  # canonical, derived, observed, external, runtime
    trust: TrustLevel = TrustLevel.TRUSTED_INTERNAL_ARTIFACT
    authority: AuthorityLevel = AuthorityLevel.ADVISORY
    freshness: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    relevance: float = 1.0  # 0.0 a 1.0
    token_cost: int = 0
    immutable: bool = False
    scope: str = "project"  # private, team, project, global
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    supersedes: List[str] = field(default_factory=list)
    superseded_by: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.token_cost:
            # Estimativa heurística rápida de tokens se não fornecido
            self.token_cost = max(1, len(self.content) // 4)
        if not self.title:
            self.title = self.id
        if not self.summary and len(self.content) > 300:
            self.summary = self.content[:300] + "..."
        elif not self.summary:
            self.summary = self.content

    def get_representation(self, level: str = "full") -> str:
        """Progressive disclosure: retorna a representação no nível de detalhe solicitado.
        
        Níveis: 'title', 'abstract', 'summary', 'full'
        """
        lvl = level.lower()
        if lvl == "title":
            return self.title
        if lvl == "abstract":
            return self.abstract or self.summary or self.title
        if lvl == "summary":
            return self.summary or self.content
        return self.content

    def calculate_score(
        self,
        task_affinity: float = 1.0,
        posture_affinity: float = 1.0,
        source_quality: float = 1.0,
    ) -> float:
        """Calcula o context value density score:
        score = (relevance * authority_weight * task_affinity * posture_affinity * source_quality) / log(cost)
        """
        auth_weight = AUTHORITY_WEIGHTS.get(self.authority, 0.2)
        numerator = (
            max(0.01, self.relevance)
            * auth_weight
            * max(0.1, task_affinity)
            * max(0.1, posture_affinity)
            * max(0.1, source_quality)
        )
        # Penaliza tokens excessivos mantendo proporção saudável
        cost_factor = max(1.0, (self.token_cost / 500.0) ** 0.5)
        return float(numerator / cost_factor)

    def render_enveloped(self, representation_level: str = "full") -> str:
        """Renderiza o item envelopado estruturalmente com seus limites de segurança.
        
        Itens externos/não-confiáveis são explicitamente neutralizados em tags de evidência externa
        para prevenir prompt injection.
        """
        rep = self.get_representation(representation_level)
        if self.trust == TrustLevel.EXTERNAL_UNTRUSTED:
            return (
                f'<external_untrusted_evidence id="{self.id}" source="{self.source_uri}">\n'
                f"{rep}\n"
                f"</external_untrusted_evidence>"
            )
        if self.trust in (TrustLevel.DOCUMENT_CONTENT, TrustLevel.MCP_RESULTS):
            return (
                f'<retrieved_data id="{self.id}" type="{self.item_type}" source="{self.source_uri}">\n'
                f"{rep}\n"
                f"</retrieved_data>"
            )
        return rep
