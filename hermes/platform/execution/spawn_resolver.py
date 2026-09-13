import uuid
from typing import Dict, Any, Optional
from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.posture.resolver import PostureResolver
from hermes.platform.models.model_resolver import ModelResolver
from hermes.platform.models.provider_router import ExactModelRouter
from hermes.platform.capabilities.resolver import CapabilityResolver
from hermes.platform.execution.assignment import AssignmentSpec
from hermes.platform.execution.classify import classify_execution
from hermes.platform.execution.preferences import (
    agent_eligibility, resolve_model_profile,
)


class AgentRequirementError(RuntimeError):
    """required_agents da task exclui a lane/agente que o resolver escolheu."""


class SpawnResolver:
    def __init__(self, posture_resolver: Optional[PostureResolver] = None,
                 model_resolver: Optional[ModelResolver] = None,
                 provider_router: Optional[ExactModelRouter] = None,
                 capability_resolver: Optional[CapabilityResolver] = None):
        self.posture_resolver = posture_resolver or PostureResolver()
        self.model_resolver = model_resolver or ModelResolver()
        self.provider_router = provider_router or ExactModelRouter()
        self.capability_resolver = capability_resolver or CapabilityResolver()

    def resolve(self, task: TaskSpec) -> AssignmentSpec:
        posture = self.posture_resolver.resolve(task.posture)

        # Model precedence (HAOS v1.1 + Emendas 8/9): explicit task binding
        # (required) > posture model > preferred (soft) > unbound. Nenhum
        # perfil resolvido -> ModelResolver rejeita None (fail-fast upstream).
        profile_id, binding_level = resolve_model_profile(
            task.model_profile, posture.model_profile, task.model_profile_preferred
        )
        profile = self.model_resolver.resolve(profile_id)
        route = self.provider_router.select_route(profile)
        caps = self.capability_resolver.resolve_requirements(task.required_capabilities)

        # Decide execution shape/lane/modo pela classificação canônica pura
        # (A4): heurística histórica por default; `task.reuse` permite
        # persistent_specialist / sub_orchestrator explícitos. Kilo domina.
        shape, lane, agent_mode = classify_execution(
            list(caps.keys()),
            task.workspace_type,
            reuse=task.reuse,
        )

        # Emenda 8: required_agents é allow-list — lane escolhida fora dela
        # falha rápido (nunca executa com agente não permitido).
        eligible, _ = agent_eligibility(task.required_agents, task.preferred_agents, lane)
        if not eligible:
            raise AgentRequirementError(
                f"lane/agent '{lane}' not in required_agents {list(task.required_agents)}"
            )

        run_id = f"R-{uuid.uuid4().hex[:8]}"

        return AssignmentSpec(
            task_id=task.id,
            task_revision=task.version,
            run_id=run_id,
            execution_shape=shape,
            lane=lane,
            agent_mode=agent_mode,
            posture_id=posture.id,
            model_profile_id=profile.id,
            resolved_model_family=profile.model_identity.family,
            resolved_model_variant=profile.model_identity.variant,
            resolved_model_revision=profile.model_identity.revision,
            binding_level=binding_level,
            preferred_agents=[a for a in task.preferred_agents],
            provider_routes=[
                {
                    "provider_id": r.provider_id,
                    "provider_model_id": r.provider_model_id,
                    "priority": r.priority,
                }
                for r in self.provider_router.ordered_routes(profile)
            ],
            selected_route={"provider_id": route.provider_id, "model_id": route.provider_model_id},
            resolved_capabilities=list(caps.keys()),
            skills_snapshot=list(posture.skills_preferred)
        )
