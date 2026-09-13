"""Model and Provider Fabric — Marco 6.

Architectural Axiom: Model != Provider.
A model is an identity with reasoning/parameter capabilities.
A provider is an execution venue (API endpoint, rate limits, latency, cost).
Routing across providers for an exact model must preserve model identity strictly
without silent model degradation.

Strict stdlib-only; PEP-420 namespace compliant (no __init__.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from hermes.platform.models.circuit_breaker import CircuitBreaker
from hermes.platform.models.profiles import ModelIdentity, ProviderRoute


class ModelDegradationPreventedError(Exception):
    """Raised when routing fails because fallback to an inferior/different model is forbidden."""
    pass


class ModelRouteExhaustedError(Exception):
    """Raised when all configured exact-model provider routes are unavailable."""
    pass


@dataclass
class ProviderPriorityEntry:
    """Entry defining provider target and precedence for a model."""
    provider_id: str
    provider_model_id: str
    priority: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelProfile:
    """Specification of a model identity, physical capabilities, and provider failover chain.

    Enforces architectural axiom: Model != Provider.
    """
    id: str
    model_name: str
    provider_priority_list: List[ProviderPriorityEntry] = field(default_factory=list)
    context_window: int = 128000
    max_output: int = 4096
    parameters: Dict[str, Any] = field(default_factory=dict)
    posture_assignments: List[str] = field(default_factory=list)
    model_identity: Optional[ModelIdentity] = None
    substitute_allowed: bool = False

    def __post_init__(self) -> None:
        if self.model_identity is None:
            # Derive default identity from model_name if not provided
            parts = self.model_name.replace(":", "/").split("/")
            family = parts[0] if parts else self.model_name
            variant = parts[1] if len(parts) > 1 else "default"
            self.model_identity = ModelIdentity(
                family=family,
                variant=variant,
                strict_identity=True,
            )

    @property
    def routes(self) -> List[ProviderRoute]:
        """Bridge to existing ProviderRoute format for backwards compatibility."""
        return [
            ProviderRoute(
                provider_id=entry.provider_id,
                provider_model_id=entry.provider_model_id,
                priority=entry.priority,
            )
            for entry in self.provider_priority_list
        ]

    def add_provider(
        self,
        provider_id: str,
        provider_model_id: str,
        priority: int = 1,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a provider destination to priority list."""
        self.provider_priority_list.append(
            ProviderPriorityEntry(
                provider_id=provider_id,
                provider_model_id=provider_model_id,
                priority=priority,
                metadata=metadata or {},
            )
        )

    def ordered_providers(self) -> List[ProviderPriorityEntry]:
        """Return provider chain ordered by ascending priority (1 = highest)."""
        return sorted(self.provider_priority_list, key=lambda p: p.priority)


@dataclass
class RoutingDecision:
    """Result of an exact model routing evaluation."""
    model_id: str
    model_name: str
    selected_provider_id: str
    selected_provider_model_id: str
    attempts_considered: int
    bypassed_providers: List[str]
    timestamp: float = field(default_factory=time.time)


class ExactModelFailoverRouter:
    """Routes requests strictly to exact-model alternative providers.

    When a primary provider trips its circuit breaker or hits rate limits,
    the router advances to the next provider serving the EXACT same model.
    Silent model degradation (switching to a weaker or different model family)
    is strictly forbidden.
    """

    def __init__(
        self,
        circuit_breaker: Optional[CircuitBreaker] = None,
        rate_limit_tracker: Optional[Dict[str, float]] = None,
        posture_assignments: Optional[Dict[str, ModelProfile]] = None,
    ) -> None:
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        # provider_id -> rate limit expiration unix timestamp
        self._rate_limits: Dict[str, float] = rate_limit_tracker if rate_limit_tracker is not None else {}
        self._routing_history: List[RoutingDecision] = []
        self._posture_assignments: Dict[str, ModelProfile] = {}
        if posture_assignments:
            self._posture_assignments.update(posture_assignments)
        else:
            self._register_default_postures()

    @classmethod
    def get_default(cls) -> "ExactModelFailoverRouter":
        """Get or initialize default ExactModelFailoverRouter singleton."""
        if not hasattr(cls, "_default_instance") or cls._default_instance is None:
            cls._default_instance = cls()
        return cls._default_instance

    def get_status_summary(self) -> Dict[str, Any]:
        """Return visual status summary for dashboard telemetry."""
        routes = {}
        for posture, profile in self._posture_assignments.items():
            providers = []
            for entry in profile.provider_priority_list:
                cb_key = self.circuit_breaker.route_key(
                    entry.provider_id,
                    profile.model_identity or ModelIdentity(profile.model_name, "default", True)
                )
                is_open = self.circuit_breaker.is_open(cb_key)
                is_rate_limited = self.is_rate_limited(entry.provider_id)
                health = "OPEN" if is_open else ("RATE_LIMITED" if is_rate_limited else "HEALTHY")
                state = "open" if is_open else "closed"
                providers.append({
                    "provider_id": entry.provider_id,
                    "target_model": entry.provider_model_id,
                    "priority": entry.priority,
                    "circuit_breaker_state": state,
                    "is_rate_limited": is_rate_limited,
                    "health": health,
                })
            routes[posture] = {
                "posture": posture,
                "model_name": profile.model_name,
                "zero_degradation_guaranteed": True,
                "providers": providers,
                "active_provider": providers[0]["provider_id"] if providers else None,
            }
        return {
            "status": "active",
            "zero_degradation_enforced": True,
            "routes": routes,
        }

    def _register_default_postures(self) -> None:
        """Register default posture-to-ModelProfile mappings.
        - architect -> ModelProfile (claude-3-7-sonnet via Anthropic -> OpenRouter -> Bedrock)
        - coder -> ModelProfile (deepseek-v3 via DeepSeek -> OpenRouter)
        - reviewer -> ModelProfile (claude-3-5-sonnet independent reviewer)
        """
        architect_profile = ModelProfile(
            id="profile-architect",
            model_name="claude-3-7-sonnet",
            context_window=200000,
            max_output=8192,
            parameters={"temperature": 0.2},
            posture_assignments=["architect"],
            model_identity=ModelIdentity(
                family="claude-3-7-sonnet",
                variant="default",
                strict_identity=True,
            ),
            provider_priority_list=[
                ProviderPriorityEntry("anthropic", "claude-3-7-sonnet", priority=1),
                ProviderPriorityEntry("openrouter", "anthropic/claude-3.7-sonnet", priority=2),
                ProviderPriorityEntry("bedrock", "anthropic.claude-3-7-sonnet-v1:0", priority=3),
            ],
        )

        coder_profile = ModelProfile(
            id="profile-coder",
            model_name="deepseek-v3",
            context_window=128000,
            max_output=8192,
            parameters={"temperature": 0.1},
            posture_assignments=["coder", "implementer"],
            model_identity=ModelIdentity(
                family="deepseek-v3",
                variant="default",
                strict_identity=True,
            ),
            provider_priority_list=[
                ProviderPriorityEntry("deepseek", "deepseek-chat", priority=1),
                ProviderPriorityEntry("openrouter", "deepseek/deepseek-chat", priority=2),
            ],
        )

        reviewer_profile = ModelProfile(
            id="profile-reviewer",
            model_name="claude-3-5-sonnet",
            context_window=200000,
            max_output=8192,
            parameters={"temperature": 0.0},
            posture_assignments=["reviewer", "refinery"],
            model_identity=ModelIdentity(
                family="claude-3-5-sonnet",
                variant="default",
                strict_identity=True,
            ),
            provider_priority_list=[
                ProviderPriorityEntry("anthropic", "claude-3-5-sonnet-latest", priority=1),
                ProviderPriorityEntry("openrouter", "anthropic/claude-3.5-sonnet", priority=2),
                ProviderPriorityEntry("bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0", priority=3),
            ],
        )

        self.register_posture_profile("architect", architect_profile)
        self.register_posture_profile("coder", coder_profile)
        self.register_posture_profile("reviewer", reviewer_profile)

    def register_posture_profile(self, posture: str, profile: ModelProfile) -> None:
        """Register or override a ModelProfile for a specific posture."""
        self._posture_assignments[posture] = profile
        if posture not in profile.posture_assignments:
            profile.posture_assignments.append(posture)

    def resolve_posture_profile(self, posture: str) -> Optional[ModelProfile]:
        """Resolve a ModelProfile assigned to a given posture."""
        return self._posture_assignments.get(posture)

    def select_route_for_posture(self, posture: str) -> RoutingDecision:
        """Select route for a given posture using exact-model failover."""
        profile = self.resolve_posture_profile(posture)
        if profile is None:
            raise KeyError(f"No ModelProfile assigned for posture: {posture}")
        return self.select_route(profile)

    def mark_rate_limited(self, provider_id: str, duration_seconds: float) -> None:
        """Mark a provider as temporarily rate-limited."""
        self._rate_limits[provider_id] = time.time() + duration_seconds

    def is_rate_limited(self, provider_id: str) -> bool:
        """Check if provider is under an active rate limit window."""
        expiry = self._rate_limits.get(provider_id, 0.0)
        if time.time() < expiry:
            return True
        if provider_id in self._rate_limits:
            del self._rate_limits[provider_id]
        return False

    def is_provider_available(self, provider_id: str, model_identity: ModelIdentity) -> bool:
        """Verify provider availability across circuit breaker and rate limiter."""
        if self.is_rate_limited(provider_id):
            return False
        cb_key = self.circuit_breaker.route_key(provider_id, model_identity)
        return not self.circuit_breaker.is_open(cb_key)

    def select_route(self, profile: ModelProfile) -> RoutingDecision:
        """Select the next available provider for the exact model defined in profile.

        Raises:
            ModelDegradationPreventedError / ModelRouteExhaustedError:
                If no provider is available for this exact model.
        """
        if not profile.provider_priority_list:
            raise ModelRouteExhaustedError(
                f"No provider routes configured for model '{profile.model_name}' (id={profile.id})."
            )

        identity = profile.model_identity or ModelIdentity(
            family=profile.model_name,
            variant="default",
            strict_identity=True,
        )

        ordered = profile.ordered_providers()
        bypassed: List[str] = []
        attempts = 0

        for entry in ordered:
            attempts += 1
            if not self.is_provider_available(entry.provider_id, identity):
                bypassed.append(entry.provider_id)
                continue

            decision = RoutingDecision(
                model_id=profile.id,
                model_name=profile.model_name,
                selected_provider_id=entry.provider_id,
                selected_provider_model_id=entry.provider_model_id,
                attempts_considered=attempts,
                bypassed_providers=bypassed,
            )
            self._routing_history.append(decision)
            return decision

        # All exact-model providers exhausted.
        # Check degradation prevention constraint:
        if not profile.substitute_allowed:
            raise ModelDegradationPreventedError(
                f"ExactModelFailoverRouter: All providers for exact model '{profile.model_name}' "
                f"({[p.provider_id for p in ordered]}) are unavailable (circuit open or rate limited). "
                f"Silent model degradation strictly prevented."
            )

        raise ModelRouteExhaustedError(
            f"All providers for model '{profile.model_name}' exhausted: {bypassed}"
        )

    def record_failure(self, provider_id: str, profile: ModelProfile, is_rate_limit: bool = False, rate_limit_cooldown: float = 60.0) -> None:
        """Record execution failure or rate limit on provider for this model."""
        identity = profile.model_identity or ModelIdentity(
            family=profile.model_name,
            variant="default",
            strict_identity=True,
        )
        if is_rate_limit:
            self.mark_rate_limited(provider_id, rate_limit_cooldown)
        else:
            cb_key = self.circuit_breaker.route_key(provider_id, identity)
            self.circuit_breaker.record_failure(cb_key)

    def record_success(self, provider_id: str, profile: ModelProfile) -> None:
        """Record success, clearing breaker and rate limit state."""
        identity = profile.model_identity or ModelIdentity(
            family=profile.model_name,
            variant="default",
            strict_identity=True,
        )
        self._rate_limits.pop(provider_id, None)
        cb_key = self.circuit_breaker.route_key(provider_id, identity)
        self.circuit_breaker.record_success(cb_key)
