from typing import List, Optional, Dict, Any, Callable

from hermes.platform.models.profiles import ModelProfile, ProviderRoute
from hermes.platform.models.circuit_breaker import CircuitBreaker

# Emenda 4: rota esgotada sobe como evento de domínio p/ o Orchestrator
# decidir. Listener registry é o seam decoupled (sem acoplamento a EventBus/
# EventStore): o runtime faz subscribe e publica onde quiser.
_EXHAUSTED_LISTENERS: List[Callable[[Dict[str, Any]], None]] = []


def subscribe_route_exhausted(listener: Callable[[Dict[str, Any]], None]) -> None:
    """Registra um listener síncrono p/ o snapshot de rota esgotada."""
    if listener not in _EXHAUSTED_LISTENERS:
        _EXHAUSTED_LISTENERS.append(listener)


def unsubscribe_route_exhausted(listener: Callable[[Dict[str, Any]], None]) -> None:
    """Remove um listener (testes usam em finally p/ não vazar entre casos)."""
    if listener in _EXHAUSTED_LISTENERS:
        _EXHAUSTED_LISTENERS.remove(listener)


def _emit_route_exhausted(snapshot: Dict[str, Any]) -> None:
    for listener in list(_EXHAUSTED_LISTENERS):
        try:
            listener(snapshot)
        except Exception:
            # Listener de observabilidade nunca quebra o router.
            continue


class ModelRouteExhaustedException(Exception):
    pass


class ExactModelRouter:
    """Selects the best *route for the same exact model* across providers.

    Never substitutes a different model: it walks the profile's route chain in
    manual priority order, skipping (model, provider) pairs whose circuit is
    open, and raises :class:`ModelRouteExhaustedException` when every route for
    that exact model identity is unavailable. Manual priority is sovereign.
    """

    def __init__(self, circuit_breaker: Optional[CircuitBreaker] = None):
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

    def select_route(self, profile: ModelProfile) -> ProviderRoute:
        sorted_routes = sorted(profile.routes, key=lambda r: r.priority)
        exhausted_providers: List[str] = []
        for route in sorted_routes:
            key = self.circuit_breaker.route_key(route.provider_id, profile.model_identity)
            if not self.circuit_breaker.is_open(key):
                return route
            exhausted_providers.append(route.provider_id)

        # Emenda 4: ANTES de levantar, emite o snapshot de domínio (payload
        # estável) para o Orchestrator decidir — listeners são no-op por padrão.
        _emit_route_exhausted({
            "model_family": profile.model_identity.family,
            "model_variant": profile.model_identity.variant,
            "model_revision": profile.model_identity.revision,
            "provider_chain": [r.provider_id for r in sorted_routes],
            "exhausted_providers": exhausted_providers,
        })
        raise ModelRouteExhaustedException(
            f"All provider routes for model {profile.model_identity.family}:{profile.model_identity.variant} "
            f"are exhausted or open in CircuitBreaker."
        )

    def ordered_routes(self, profile: ModelProfile) -> List[ProviderRoute]:
        """Full route chain in manual-priority order (used for AssignmentSpec snapshots)."""
        return sorted(profile.routes, key=lambda r: r.priority)


ProviderRouter = ExactModelRouter  # ADR-002 canonical alias
