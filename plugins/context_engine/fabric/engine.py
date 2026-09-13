"""Plugin entrypoint para o ContextEngine 'fabric' descoberto pelo Hermes upstream.

Quando o usuário configurar:
context:
  engine: fabric

O Hermes carrega este plugin através de `plugins.context_engine.load_context_engine('fabric')`.
"""

from __future__ import annotations

from typing import Any

from hermes.platform.context.engine.fabric_engine import FabricContextEngine


def register(ctx: Any) -> None:
    """Registra o FabricContextEngine no PluginContext de context_engine."""
    engine = FabricContextEngine(posture_name="coder")
    ctx.register_context_engine(engine)
