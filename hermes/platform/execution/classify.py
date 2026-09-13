"""A4 — decisão de execução (shape/lane/modo) como função pura compartilhada.

Hoje a heurística vive em dois lugares (SpawnResolver + lane_for_spec); este
módulo é a versão canônica determinística que aceita `reuse` explícito:
- reuse="none"        -> comportamento histórico (ephemeral_worker/hermes,
                         worker_lane/kilo quando git).
- reuse="persistent"  -> shape persistent_specialist (agente residente), só
                         quando a lane NÃO é kilo (kilo é sempre worker_lane).
- reuse="orchestrator"-> shape sub_orchestrator (agente que delega/sub-agentes),
                         mesma restrição.
A lane kilo (git) domina qualquer reuse: shape worker_lane é a fronteira real
do executor agêntico da lane kilo. Valores inválidos falham rápido.
"""

from typing import List, Optional, Tuple

LANE_HERMES = "hermes"
LANE_KILO = "kilo"
LANE_VISION = "vision"
LANE_ANP = "anp"
LANE_A2A = "a2a"

REUSE_NONE = "none"
REUSE_PERSISTENT = "persistent"
REUSE_ORCHESTRATOR = "orchestrator"
REUSE_SELF = "self"
REUSE_POSTURE_SWITCH = "posture_switch"
REUSE_CAPABILITY_WORKER = "capability_worker"
REUSE_EXTERNAL_AGENT = "external_agent"
REUSE_VALUES = (
    REUSE_NONE,
    REUSE_PERSISTENT,
    REUSE_ORCHESTRATOR,
    REUSE_SELF,
    REUSE_POSTURE_SWITCH,
    REUSE_CAPABILITY_WORKER,
    REUSE_EXTERNAL_AGENT,
)

SHAPE_SELF = "self"
SHAPE_POSTURE_SWITCH = "posture_switch"
SHAPE_PERSISTENT = "persistent_specialist"
SHAPE_EPHEMERAL = "ephemeral_worker"
SHAPE_SUB_ORCH = "sub_orchestrator"
SHAPE_WORKER_LANE = "worker_lane"
SHAPE_CAPABILITY_WORKER = "capability_worker"
SHAPE_EXTERNAL_AGENT = "external_agent"


class InvalidReuseError(ValueError):
    pass


def classify_execution(
    required_caps: List[str],
    workspace_type: Optional[str] = None,
    *,
    reuse: str = REUSE_NONE,
) -> Tuple[str, str, str]:
    """(execution_shape, lane, agent_mode) determinístico cobrindo as 8 formas.

    Invariantes:
    1. Kilo domina para desenvolvimento de software / git / worktree quando não for
       especificamente delegado como self ou external.
    2. Modality agentica (vision) sem git direciona para capability worker/vision lane.
    3. External agent (ANP/A2A) mapeia para suas respectivas lanes.
    4. Sub-orquestrador, persistent specialist, self e posture switch mapeados transparentemente.
    """
    if reuse not in REUSE_VALUES:
        raise InvalidReuseError(
            f"reuse must be one of {list(REUSE_VALUES)}, got {reuse!r}"
        )
    caps = set(required_caps or [])
    git = "git" in caps or workspace_type == "git_worktree"

    if reuse == REUSE_SELF:
        return SHAPE_SELF, LANE_HERMES, "ephemeral"
    if reuse == REUSE_POSTURE_SWITCH:
        return SHAPE_POSTURE_SWITCH, LANE_HERMES, "ephemeral"
    if reuse == REUSE_EXTERNAL_AGENT:
        lane = LANE_ANP if "anp" in caps else (LANE_A2A if "a2a" in caps else LANE_ANP)
        return SHAPE_EXTERNAL_AGENT, lane, "ephemeral"
    if reuse == REUSE_CAPABILITY_WORKER or ("image-understanding" in caps and not git):
        return SHAPE_CAPABILITY_WORKER, LANE_VISION, "ephemeral"

    if git:
        return SHAPE_WORKER_LANE, LANE_KILO, "ephemeral"
    if reuse == REUSE_PERSISTENT:
        return SHAPE_PERSISTENT, LANE_HERMES, "persistent"
    if reuse == REUSE_ORCHESTRATOR:
        return SHAPE_SUB_ORCH, LANE_HERMES, "ephemeral"
    return SHAPE_EPHEMERAL, LANE_HERMES, "ephemeral"
