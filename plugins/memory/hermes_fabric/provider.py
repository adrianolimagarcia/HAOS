"""Plugin entrypoint para o MemoryProvider 'hermes-fabric' descoberto pelo Hermes upstream.

Permite configurar no config.yaml:
memory:
  provider: hermes-fabric
"""

from __future__ import annotations

from typing import Any

from hermes.platform.context.memory.provider import HermesFabricMemoryProvider


def register(ctx: Any) -> None:
    """Registra o HermesFabricMemoryProvider no PluginContext de memory."""
    provider = HermesFabricMemoryProvider()
    ctx.register_memory_provider(provider)
