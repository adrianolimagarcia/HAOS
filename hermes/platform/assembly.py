"""Assembléia do runtime HAOS (Fase 1 wire) — composição de registries/providers.

O ``CapabilityRegistry`` mantém os defaults SEM conhecer workers (decoupling do
VisionWorker). A assembléia é o ponto onde o runtime aditivo liga os providers
HAOS ao registry: ``deep-research`` (agentic) via ``register_research_provider``.

Fail-closed preservado: registrar o provider SEM fetcher é válido (probe diz
``available: False`` e ``acquire`` lança); o fetcher de produção é injetado no
assembly do deployment (adapter out-of-process, forma B do INTEGRATIONS).
"""

from typing import Optional

from hermes.platform.capabilities.registry import (
    CapabilityRegistry, CapabilityResolver,
)
from hermes.platform.capabilities.research.worker import (
    ResearchProvider, register_research_provider,
)


def build_capability_registry(
    research_provider: Optional[ResearchProvider] = None,
) -> CapabilityRegistry:
    """Registry com defaults do kernel + capability ``deep-research``."""
    registry = CapabilityRegistry()
    register_research_provider(registry, research_provider)
    return registry


def build_capability_resolver(
    research_provider: Optional[ResearchProvider] = None,
) -> CapabilityResolver:
    """Resolver pronto para o runtime (defaults + research registrados)."""
    return CapabilityResolver(build_capability_registry(research_provider))
