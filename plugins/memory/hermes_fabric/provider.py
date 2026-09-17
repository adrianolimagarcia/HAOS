"""Plugin entrypoint para o MemoryProvider 'hermes-fabric' descoberto pelo Hermes upstream.

Permite configurar no config.yaml:
memory:
  provider: hermes-fabric
"""

from __future__ import annotations

from typing import Any

from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator


def register(ctx: Any) -> None:
    """Register the coordinator-owned provider, not an independent writer."""
    coordinator = FederatedMemoryCoordinator()
    # Keep the coordinator alive: it owns the canonical SQLite connection and
    # durable projection recovery for the lifetime of the plugin context.
    setattr(ctx, "_haos_memory_coordinator", coordinator)
    ctx.register_memory_provider(coordinator.memory_provider)
