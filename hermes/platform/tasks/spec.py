from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

# TaskSpec -> execution/contracts.py é o único acoplamento tasks->execution;
# contracts.py é módulo folha (não importa nada do platform), então não há
# ciclo. Contrato de I/O tipado (Fase 1, padrão Agno input/output_schema).
from hermes.platform.execution.contracts import TaskIOContract
from hermes.platform.execution.classify import InvalidReuseError, REUSE_VALUES

@dataclass
class AcceptanceCriterion:
    id: str
    description: str
    type: str # structural | test | lsp | review_score
    command: Optional[str] = None

@dataclass
class ReviewStage:
    id: str
    when: str = "always"
    posture: str = "reviewer"
    model_profile: str = "review-primary"
    independence: str = "fresh_context" # fresh_context | shared

@dataclass
class DependencyEdge:
    source_task: str
    target_task: str
    kind: str = "requires"  # requires | informs | produces_for | review_of | invalidates
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        valid_kinds = ("requires", "informs", "produces_for", "review_of", "invalidates")
        if self.kind not in valid_kinds:
            raise ValueError(f"Invalid DependencyEdge kind {self.kind!r}; expected one of {valid_kinds}")

@dataclass
class TaskSpec:
    id: str
    title: str
    goal: str
    description: str = ""
    team_id: str = "default"
    created_by: str = "orchestrator"
    parent_id: Optional[str] = None
    priority: int = 50
    risk_level: str = "medium" # low | medium | high | critical
    task_class: str = "software" # software | research | ops

    posture: str = "implementer"
    strategy: str = "auto" # auto | direct | single_worker | orchestrated | parallel | ensemble | goal
    # Model precedence (HAOS v1.1): explicit task binding > posture model > team default > global default.
    # None means "let the posture/team decide"; set a value only to force a specific profile for this task.
    model_profile: Optional[str] = None

    required_capabilities: List[str] = field(default_factory=list)
    preferred_capabilities: List[str] = field(default_factory=list)
    required_modalities: List[str] = field(default_factory=lambda: ["text"])
    optional_modalities: List[str] = field(default_factory=list)
    
    preferred_skills: List[str] = field(default_factory=list)
    mcp_packs: List[str] = field(default_factory=list)
    
    context_files: List[str] = field(default_factory=list)
    context_decisions: List[str] = field(default_factory=list)
    context_symbols: List[str] = field(default_factory=list)
    context_memory_queries: List[str] = field(default_factory=list)

    requires_tasks: List[str] = field(default_factory=list)
    informs_tasks: List[str] = field(default_factory=list)
    typed_dependencies: List[DependencyEdge] = field(default_factory=list)

    workspace_type: str = "git_worktree"
    acceptance_criteria: List[AcceptanceCriterion] = field(default_factory=list)
    review_stages: List[ReviewStage] = field(default_factory=list)
    
    max_cost_usd: float = 5.0
    max_runtime_minutes: int = 60
    max_runs: int = 4
    max_child_tasks: int = 8
    
    version: int = 1

    # Autonomia e flags de delegação
    allow_posture_switch: bool = True
    allow_delegation: bool = True
    allow_child_tasks: bool = True

    # Entregáveis esperados e tags
    expected_artifacts: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    # Contrato tipado de I/O para a fronteira TaskSpec -> lane (Fase 1, Agno):
    # None == contrato vazio (aceita qualquer payload/resultado).
    task_contract: Optional[TaskIOContract] = None

    # CrewAI pattern: Especificação estrita de saída esperada e inputs de upstream
    expected_output: Optional[str] = None
    upstream_inputs: Optional[Dict[str, Any]] = None

    # Emendas 8/9 — agente e modelo required vs preferred (soft):
    # required_agents  = agentes/lanes OBRIGATÓRIOS (vazio = qualquer).
    # preferred_agents = ordem de preferência entre candidatos elegíveis
    #                    (nunca exclui; só ordena).
    # model_profile_preferred = perfil SOFT: usado SOMENTE quando não há
    #                           binding required nem postura com perfil;
    #                           nunca sobrescreve required/postura.
    required_agents: List[str] = field(default_factory=list)
    preferred_agents: List[str] = field(default_factory=list)
    model_profile_preferred: Optional[str] = None

    # A4 — forma de execução pedida pelo autor (valores validados em
    # execution/classify.py): none | persistent | orchestrator.
    # none = heurística histórica; persistent = agente residente;
    # orchestrator = agente que delega/sub-agentes. A lane kilo (git) domina.
    reuse: str = "none"

    def __post_init__(self):
        valid_strategies = ("auto", "direct", "single_worker", "orchestrated", "parallel", "ensemble", "goal")
        if self.strategy not in valid_strategies:
            raise ValueError(f"Invalid TaskSpec strategy {self.strategy!r}; expected one of {valid_strategies}")

        if self.reuse not in REUSE_VALUES:
            raise InvalidReuseError(f"Invalid TaskSpec reuse {self.reuse!r}; expected one of {list(REUSE_VALUES)}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskSpec":
        """Reconstrói uma TaskSpec a partir de um dicionário serializado."""
        d = dict(data)
        # Reconstrói AcceptanceCriterion se lista de dicts
        if "acceptance_criteria" in d and d["acceptance_criteria"]:
            d["acceptance_criteria"] = [
                AcceptanceCriterion(**ac) if isinstance(ac, dict) else ac
                for ac in d["acceptance_criteria"]
            ]
        # Reconstrói ReviewStage se lista de dicts
        if "review_stages" in d and d["review_stages"]:
            d["review_stages"] = [
                ReviewStage(**rs) if isinstance(rs, dict) else rs
                for rs in d["review_stages"]
            ]
        # Reconstrói DependencyEdge se lista de dicts
        if "typed_dependencies" in d and d["typed_dependencies"]:
            d["typed_dependencies"] = [
                DependencyEdge(**de) if isinstance(de, dict) else de
                for de in d["typed_dependencies"]
            ]
        # Filtra chaves desconhecidas para compatibilidade robusta
        import inspect
        sig = inspect.signature(cls.__init__)
        valid_keys = set(sig.parameters.keys()) - {"self"}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)
