"""HAOS TeamSpec — papéis de time (Fase 1, port clean-room do conceito GasTown).

GasTown organiza agentes em papéis canônicos (town: mayor/deacon/dog; rig:
witness/refinery/polecat/crew), cada um com escopo, verbo, consumo/produção e
um **modelo por função** que é decidido por config/CLI (aliases ``--model``,
``GT_DEFAULT_MODEL``), não hardcoded no papel. O HAOS já cobre modelo-por-
postura, workers por claim/lane (K1) e o gate de review único do Kanban; o que
este módulo acrescenta (camada aditiva, sem duplicar o kernel) é:

1. **Roster nomeado** ``TeamSpec``: vincula ``role_id -> posture + model +
   lane + independence + cardinalidade``, com resolução e validação.
2. **Tabela de modelo por papel explícita** (``ROLE_MODEL_MAP``), espelhando a
   lição de custo do GasTown: papéis prescritivos/patrulha (deacon/dog) rodam
   em tier barato, papéis de gate (witness/refinery) em tier de revisão.
3. **DAG de gates** (mayor -> polecat -> witness -> refinery): ordena os gates
   de revisão que o ``ReviewStage`` do TaskSpec declara mas o HAOS nunca
   executa em ordem; downstream só fica ``ready`` após veredito de upstream.

Regras do port (INTEGRATIONS.md §3, GasTown row): o conceito entra, código Go
não é copiado; stdlib-only; nada de runtime externo no processo. ``TeamSpec``
não roda supervisor novo: cada ``TeamRole`` é um binding nomeado — o trabalho
continua fluindo como cards claimados por workers de lane (K1), e paralelismo
= multiplicity ``many`` + vários workers, serialização de refinery = gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Papéis canônicos (espelho conceitual de GasTown internal/config/roles.go).
# Escopos: town = {mayor, deacon, dog}; rig = {witness, refinery, polecat, crew}.
# --------------------------------------------------------------------------- #
CANONICAL_ROLE_IDS: Tuple[str, ...] = (
    "mayor", "deacon", "dog", "witness", "refinery", "polecat", "crew",
)
TOWN_ROLES: Tuple[str, ...] = ("mayor", "deacon", "dog")
RIG_ROLES: Tuple[str, ...] = ("witness", "refinery", "polecat", "crew")

# Papéis de cardinalidade "one": no máximo uma cadeira por time.
ONE_MULTIPLICITY_ROLES: Tuple[str, ...] = ("mayor", "deacon", "witness", "refinery")
# Papéis "many": fan-out permitido (polecat transitório, dog sob deacon, crew opcional).
MANY_MULTIPLICITY_ROLES: Tuple[str, ...] = ("dog", "polecat", "crew")

# Papel -> postura HAOS (novas posturas `supervisor`/`refinery` em posture/specs.py).
ROLE_POSTURE_MAP: Dict[str, str] = {
    "mayor": "executive",      # orquestrador global (postura executive existente)
    "deacon": "supervisor",    # novo: patrulha/watchdog/gate-routing
    "dog": "default",          # worker de infra sob deacon, one-shot
    "witness": "reviewer",     # verificação independente (flags existentes já encaixam)
    "refinery": "refinery",    # novo: fila de merge com gates de verificação
    "polecat": "implementer",  # implementa uma tarefa no próprio worktree
    "crew": "executive",       # deputy-mayor opcional/persistente
}

# Papel -> modelo por função (default do time; overridable por TeamRole).
# Deacon/dog = prescritivo/patrulha -> tier barato (coding-primary, deepseek-v4-flash);
# witness/refinery = gate de revisão -> review-primary (gpt-4o);
# mayor/crew = orquestração -> orchestrator-primary (glm-5.3);
# polecat = implementação -> coding-primary.
ROLE_MODEL_MAP: Dict[str, str] = {
    "mayor": "orchestrator-primary",
    "deacon": "coding-primary",
    "dog": "coding-primary",
    "witness": "review-primary",
    "refinery": "review-primary",
    "polecat": "coding-primary",
    "crew": "orchestrator-primary",
}

# Papel -> lane de execução (mesmos ids de lane_executor: hermes | kilo).
# refinery e polecat mexem em git/worktree -> kilo; resto -> hermes.
ROLE_LANE_MAP: Dict[str, str] = {
    "mayor": "hermes",
    "deacon": "hermes",
    "dog": "hermes",
    "witness": "hermes",
    "refinery": "kilo",
    "polecat": "kilo",
    "crew": "hermes",
}

# Regras de composição: se um papel X está no time, o papel Y também deve estar.
# Fiel ao GasTown: refinery exige witness (nunca mexe sem veredito), polecat
# exige witness (não fecha a própria issue), dog exige deacon (não faz free-lance).
# O GasTown NÃO exige mayor em time mínimo de rig (patrulha roda sem mayor).
COMPOSITION_RULES: Tuple[Tuple[str, str], ...] = (
    ("refinery", "witness"),
    ("polecat", "witness"),
    ("dog", "deacon"),
)

# DAG de gates do time default: fluxo de artefato mayor -> polecat -> witness ->
# refinery. Cada aresta (a, b) = "a saída de a alimenta o gate de b"; o grafo
# deve ser acíclico e todo endpoint presente no roster.
DEFAULT_GATE_EDGES: Tuple[Tuple[str, str], ...] = (
    ("mayor", "polecat"),
    ("polecat", "witness"),
    ("witness", "refinery"),
)

DEFAULT_TEAM_ID = "default"
INDEPENDENCE_SHARED = "shared"
INDEPENDENCE_FRESH = "fresh_context"


class TeamError(Exception):
    """Erro base do modelo de times."""


class TeamValidationError(TeamError):
    """Time inválido: papel desconhecido, postura/perfil inexistente, cardinalidade,
    regra de composição ou ciclo de gate."""


@dataclass(frozen=True)
class TeamRole:
    """Uma cadeira nomeada no time: papel canônico ligado a postura/modelo/lane.

    ``posture_id``/``model_profile``/``lane``/``multiplicity`` None herdam da
    tabela canônica (ROLE_*_MAP / cardinalidade do papel); valores explícitos
    sobrescrevem (time customiza o modelo por função, como aliases --model do
    GasTown).
    """

    role_id: str
    posture_id: Optional[str] = None      # None -> ROLE_POSTURE_MAP[role_id]
    model_profile: Optional[str] = None   # None -> ROLE_MODEL_MAP[role_id] (tabela canônica)
    lane: Optional[str] = None            # None -> ROLE_LANE_MAP[role_id]
    independence: str = INDEPENDENCE_SHARED  # shared | fresh_context
    multiplicity: Optional[str] = None    # None -> cardinalidade canônica (one|many)
    required: bool = True                 # False => cadeira pode ficar vazia


def _effective_multiplicity(role_id: str, explicit: Optional[str]) -> str:
    if explicit is not None:
        return explicit
    return "many" if role_id in MANY_MULTIPLICITY_ROLES else "one"


@dataclass(frozen=True)
class TeamSpec:
    """Time nomeado: roster de papéis + DAG de gates opcional.

    ``TaskSpec.team_id`` (tasks/spec.py) e ``MessageEnvelope.team_id``
    (observability/events.py) já referenciam esse id; o resolver valida que o
    id existe antes de o dispatcher escalar trabalho para o time.
    """

    team_id: str
    name: str
    description: str = ""
    scope: str = "rig"                    # town | rig | any
    roles: List[TeamRole] = field(default_factory=list)
    gate_edges: Tuple[Tuple[str, str], ...] = tuple(DEFAULT_GATE_EDGES)
    default_team: bool = False


class TeamResolver:
    """Registra/resolve/valida TeamSpecs (espelho de PostureResolver/ModelResolver).

    Time desconhecido levanta ``TeamValidationError`` listando os times
    registrados (nunca fallback silencioso). Validação usa resolvers REAIS
    (PostureResolver + ModelResolver) injetáveis para checar posturas e perfis
    sem duplicar tabelas hardcoded.
    """

    def __init__(self, posture_resolver=None, model_resolver=None):
        from hermes.platform.posture.specs import PostureResolver
        from hermes.platform.models.model_resolver import ModelResolver
        self._teams: Dict[str, TeamSpec] = {}
        self._posture_resolver = posture_resolver if posture_resolver is not None else PostureResolver()
        self._model_resolver = model_resolver if model_resolver is not None else ModelResolver()

    # -- registro / resolução ------------------------------------------------
    def register(self, team: TeamSpec) -> None:
        if team.team_id in self._teams:
            raise TeamValidationError(f"Team '{team.team_id}' already registered")
        self._teams[team.team_id] = team

    def resolve(self, team_id: str) -> TeamSpec:
        team = self._teams.get(team_id)
        if team is None:
            raise TeamValidationError(
                f"Unknown team '{team_id}'. Registered: {sorted(self._teams)}"
            )
        return team

    def registered_ids(self) -> List[str]:
        return sorted(self._teams)

    # -- validação -----------------------------------------------------------
    def validate(self, team: TeamSpec) -> None:
        """Valida o time contra tabelas canônicas + resolvers reais.

        Relações (não snapshots): todo papel é canônico, toda postura mapeada
        existe no PostureResolver, todo perfil de modelo resolve no
        ModelResolver, regras de composição, cardinalidade e DAG de gates.
        """
        role_ids = [r.role_id for r in team.roles]
        if len(role_ids) != len(set(role_ids)):
            raise TeamValidationError(f"Team '{team.team_id}' has duplicate role ids")
        role_set = set(role_ids)

        for role in team.roles:
            if role.role_id not in CANONICAL_ROLE_IDS:
                raise TeamValidationError(
                    f"Unknown role '{role.role_id}' (canonical: {CANONICAL_ROLE_IDS})"
                )
            effective_multiplicity = _effective_multiplicity(role.role_id, role.multiplicity)
            canonical_multiplicity = (
                "many" if role.role_id in MANY_MULTIPLICITY_ROLES else "one"
            )
            if role.multiplicity is not None and effective_multiplicity != canonical_multiplicity:
                raise TeamValidationError(
                    f"Role '{role.role_id}' declares multiplicity "
                    f"'{effective_multiplicity}' but canonical is '{canonical_multiplicity}'"
                )
            posture_id = role.posture_id or ROLE_POSTURE_MAP.get(role.role_id, "")
            if posture_id not in self._posture_resolver.registered_ids():
                raise TeamValidationError(
                    f"Role '{role.role_id}' maps to unknown posture '{posture_id}'"
                )
            model_profile = role.model_profile or ROLE_MODEL_MAP.get(role.role_id, "")
            try:
                self._model_resolver.resolve(model_profile)
            except Exception as exc:  # UnknownModelProfileError -> TeamValidationError
                raise TeamValidationError(
                    f"Role '{role.role_id}' maps to unknown model profile "
                    f"'{model_profile}': {exc}"
                ) from exc
            if role.independence not in (INDEPENDENCE_SHARED, INDEPENDENCE_FRESH):
                raise TeamValidationError(
                    f"Role '{role.role_id}' independence must be shared|fresh_context"
                )

        # Regras de composição (X presente => Y presente).
        for required_role, dependency in COMPOSITION_RULES:
            if required_role in role_set and dependency not in role_set:
                raise TeamValidationError(
                    f"Team '{team.team_id}' has role '{required_role}' but lacks "
                    f"'{dependency}' (composition rule)"
                )

        # DAG de gates: endpoints presentes + acíclico.
        for edge in team.gate_edges:
            if len(edge) != 2:
                raise TeamValidationError(
                    f"Team '{team.team_id}' has malformed gate edge {edge!r}"
                )
            for endpoint in edge:
                if endpoint not in role_set:
                    raise TeamValidationError(
                        f"Team '{team.team_id}' gate edge {edge!r} references role "
                        f"'{endpoint}' not in the roster"
                    )
        self._assert_acyclic(team.team_id, team.gate_edges)

    @staticmethod
    def _assert_acyclic(team_id: str, edges: Tuple[Tuple[str, str], ...]) -> None:
        adjacency: Dict[str, List[str]] = {}
        for a, b in edges:
            adjacency.setdefault(a, []).append(b)
        visiting, done = set(), set()

        def dfs(node: str) -> None:
            if node in visiting:
                raise TeamValidationError(
                    f"Team '{team_id}' gate edges contain a cycle at '{node}'"
                )
            if node in done:
                return
            visiting.add(node)
            for nxt in adjacency.get(node, []):
                dfs(nxt)
            visiting.discard(node)
            done.add(node)

        for node in list(adjacency):
            dfs(node)

    # -- ligação papel -> binding resolvido ----------------------------------
    def binding_for(
        self,
        team: TeamSpec,
        role_id: str,
        *,
        task_model_profile: Optional[str] = None,
        task_model_profile_preferred: Optional[str] = None,
    ) -> Dict[str, str]:
        """Resolve a cadeira do papel num dict de binding efetivo.

        Precedência de modelo (Emendas 8/9, espelha o SpawnResolver): binding
        required da task > override do TeamRole > tabela canônica do papel >
        preferred (soft, só quando nada resolveu) > unbound. Postura/lane
        herdadas da tabela canônica com override por TeamRole. ``binding_level``
        registra qual camada decidiu o perfil."""
        role = next((r for r in team.roles if r.role_id == role_id), None)
        if role is None:
            raise TeamValidationError(
                f"Role '{role_id}' not in team '{team.team_id}'"
            )
        required = (
            task_model_profile
            or role.model_profile
            or ROLE_MODEL_MAP.get(role_id)
        )
        if required:
            model_profile = required
            binding_level = "required"
        elif task_model_profile_preferred:
            model_profile = task_model_profile_preferred
            binding_level = "preferred"
        else:
            model_profile = None
            binding_level = "unbound"
        return {
            "role_id": role_id,
            "posture_id": role.posture_id or ROLE_POSTURE_MAP.get(role_id, ""),
            "model_profile": model_profile,
            "lane": role.lane or ROLE_LANE_MAP.get(role_id, "hermes"),
            "independence": role.independence,
            "multiplicity": role.multiplicity,
            "binding_level": binding_level,
        }


# --------------------------------------------------------------------------- #
# Time default (template canônico, como `gt up` levanta o town/rig completo).
# --------------------------------------------------------------------------- #
def default_team_roles() -> List[TeamRole]:
    return [
        TeamRole(
            role_id=role_id,
            posture_id=ROLE_POSTURE_MAP[role_id],
            model_profile=ROLE_MODEL_MAP[role_id],
            lane=ROLE_LANE_MAP[role_id],
            independence=(
                INDEPENDENCE_FRESH if role_id in ("witness", "dog") else INDEPENDENCE_SHARED
            ),
            multiplicity=(
                "many" if role_id in MANY_MULTIPLICITY_ROLES else "one"
            ),
            required=(role_id != "crew"),
        )
        for role_id in CANONICAL_ROLE_IDS
    ]


def default_team() -> TeamSpec:
    return TeamSpec(
        team_id=DEFAULT_TEAM_ID,
        name="Default Town+Rig",
        description=(
            "Template canônico (GasTown town mayor/deacon/dog + rig "
            "witness/refinery/polecat/crew) mapeado para posturas HAOS com "
            "modelo por função explícito e DAG de gates mayor->polecat->"
            "witness->refinery."
        ),
        scope="any",
        roles=default_team_roles(),
        gate_edges=DEFAULT_GATE_EDGES,
        default_team=True,
    )


def register_default_team(resolver: TeamResolver) -> TeamResolver:
    """Registra o time default num resolver (e valida) — conveniência."""
    team = default_team()
    resolver.validate(team)
    resolver.register(team)
    return resolver
