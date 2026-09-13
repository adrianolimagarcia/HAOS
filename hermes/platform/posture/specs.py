from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class PostureSpec:
    id: str
    name: str
    description: str
    model_binding_behavior: str = "profile" # profile | inherit
    model_profile: str = "default"
    prompt_overlay: List[str] = field(default_factory=list)
    skills_preferred: List[str] = field(default_factory=list)
    capabilities_prefer: List[str] = field(default_factory=list)
    challenge_previous_agent: bool = False
    requires_independent_review: bool = False

class PostureResolver:
    def __init__(self):
        self._postures: Dict[str, PostureSpec] = {}
        self._register_defaults()

    def register(self, posture: PostureSpec):
        self._postures[posture.id] = posture

    def resolve(self, posture_id: str) -> PostureSpec:
        posture = self._postures.get(posture_id)
        if posture is None:
            raise KeyError(
                f"Unknown posture '{posture_id}'. Registered: {sorted(self._postures)}"
            )
        return posture

    def registered_ids(self) -> List[str]:
        return sorted(self._postures)

    def _register_defaults(self):
        # Default posture: used when a task declares no posture; binds a sane
        # baseline profile instead of an unresolvable "default" id.
        self.register(PostureSpec(
            id="default",
            name="Default",
            description="Baseline posture with no specialization.",
            model_profile="coding-primary"
        ))
        self.register(PostureSpec(
            id="executive",
            name="Executive Orchestrator",
            description="High-level planning, delegation, budget and team management.",
            model_profile="orchestrator-primary",
            prompt_overlay=["system.constitution", "posture.executive"]
        ))
        self.register(PostureSpec(
            id="architect",
            name="Software Architect",
            description="System design, ADRs, simplicity and clean architecture.",
            model_profile="architecture-primary",
            prompt_overlay=["system.constitution", "posture.architect"],
            skills_preferred=["codebase-inspection", "simplify-code"],
            capabilities_prefer=["code-intelligence", "graphrag"]
        ))
        self.register(PostureSpec(
            id="implementer",
            name="Code Implementer",
            description="Clean code, LSP symbols, unit tests and precision implementation.",
            model_profile="coding-primary",
            prompt_overlay=["system.constitution", "posture.implementer"],
            skills_preferred=["test-driven-development", "systematic-debugging"],
            capabilities_prefer=["code-intelligence", "terminal", "git"]
        ))
        self.register(PostureSpec(
            id="reviewer",
            name="Code Reviewer",
            description="Adversarial code review, verification against acceptance criteria.",
            model_profile="review-primary",
            prompt_overlay=["system.constitution", "posture.reviewer"],
            skills_preferred=["requesting-code-review"],
            challenge_previous_agent=True,
            requires_independent_review=True
        ))
        self.register(PostureSpec(
            id="security-reviewer",
            name="Security Specialist",
            description="Vulnerability audit, side-effect checking and prompt injection scanning.",
            model_profile="security-primary",
            prompt_overlay=["system.constitution", "posture.security"]
        ))
        # GasTown-inspired team seats (Fase 1 — TeamSpec): two roles that are
        # not plain code roles map to their own postures instead of reusing
        # implementer/reviewer, so a team can bind them to a different model
        # per function (GasTown leaves that as config; HAOS makes it explicit).
        self.register(PostureSpec(
            id="supervisor",
            name="Supervisor (Deacon)",
            description="Cross-rig watchdog: patrol, health/stuck detection, gate routing "
                        "and escalation. Formula-driven and prescriptive — a downgrade "
                        "candidate (cheap tier) like GasTown's official patrol roles.",
            model_profile="coding-primary",
            prompt_overlay=["system.constitution", "posture.supervisor"],
            capabilities_prefer=["terminal", "git"]
        ))
        self.register(PostureSpec(
            id="refinery",
            name="Refinery (Merge Gate)",
            description="Sequential land queue: rebase, deterministic verification gates, "
                        "then merge. Never writes application code; deterministic "
                        "acceptance first, LLM only on conflict/triage.",
            model_profile="review-primary",
            prompt_overlay=["system.constitution", "posture.refinery"],
            skills_preferred=["test-driven-development"],
            capabilities_prefer=["git", "code-intelligence", "terminal"],
            requires_independent_review=True
        ))
        self.register(PostureSpec(
            id="ponytail",
            name="Lazy Senior Developer (Ponytail)",
            description="The best code is the code you never wrote. YAGNI, stdlib first, zero unrequested abstractions, -54% LOC.",
            model_profile="coding-primary",
            prompt_overlay=["system.constitution", "posture.ponytail"],
            skills_preferred=["ponytail", "ponytail-review"],
            capabilities_prefer=["terminal", "code-intelligence", "git"]
        ))
