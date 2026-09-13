from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class AssignmentSpec:
    """Frozen snapshot of the runtime execution decision (HAOS v1.1).

    Task = *what we want*; Assignment = *how the resolvers decided to execute
    it*. It must be fully reproducible: posture/profile versions, the complete
    ordered provider route chain for the exact model, resolved capabilities,
    context package, workspace and policy snapshots.
    """

    task_id: str
    task_revision: int
    run_id: str
    execution_shape: str # self | posture_switch | persistent_specialist | ephemeral_worker | sub_orchestrator | worker_lane | capability_worker | external_agent
    lane: str # hermes | kilo | vision | container | anp
    agent_mode: str # ephemeral | persistent
    posture_id: str
    model_profile_id: str
    resolved_model_family: str
    resolved_model_variant: str
    posture_version: int = 1
    model_profile_version: int = 1
    resolved_model_revision: str = "latest"
    # Complete route chain in manual priority order (not just the selected hop).
    provider_routes: List[Dict[str, Any]] = field(default_factory=list)
    selected_route: Optional[Dict[str, Any]] = None
    resolved_capabilities: List[str] = field(default_factory=list)
    workspace_uri: str = ""
    workspace_base_commit: str = ""
    context_package_id: str = ""
    context_package_hash: str = ""
    plugins_snapshot: List[str] = field(default_factory=list)
    skills_snapshot: List[str] = field(default_factory=list)
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)
    # Emendas 8/9 (aditivo): nível do binding de modelo + preferidos de agente.
    binding_level: str = ""       # required | posture | preferred | unbound
    preferred_agents: List[str] = field(default_factory=list)
