"""Plugin entrypoint para o MemoryProvider 'hermes_fabric' descoberto pelo Hermes upstream.

Permite configurar no config.yaml:

    memory:
      provider: hermes_fabric
      embedder:
        kind: hashing        # hashing (default, offline) | remote
        model_id: haos-hashing-v1-256
        dimensions: 256
        normalize: true
        reindex_policy: lazy # lazy | strict
        # kind: remote
        # base_url: http://127.0.0.1:8080
        # api_key_env: HAOS_EMBEDDER_KEY

The embedder is resolved here, at the plugin edge, because the fabric core is
stdlib-only: it must not know how Hermes stores configuration. What the core
does own is the *identity* of the vectors, so whatever this resolves is pinned
into the index and changing it is a reindex — never a silent mix of
incomparable vectors.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from hermes.platform.context.memory.embedding import (
    EmbeddingModel,
    HashingEmbedder,
    RemoteEmbedder,
    REINDEX_LAZY,
    REINDEX_STRICT,
)
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator

logger = logging.getLogger(__name__)

DEFAULT_DIMENSIONS = 256


def _memory_config() -> Dict[str, Any]:
    """The ``memory:`` subtree of the active profile's config, best effort.

    Config resolution is a core concern and its failure modes are not worth
    failing memory startup over, so anything unexpected degrades to defaults.
    """
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
    except Exception:  # noqa: BLE001
        logger.debug("Memory embedder config unavailable; using defaults", exc_info=True)
        return {}
    section = config.get("memory")
    return section if isinstance(section, dict) else {}


def _resolve_embedder(config: Optional[Dict[str, Any]] = None) -> Any:
    """Build the configured embedder, defaulting to the offline hashing one.

    A misconfigured embedder is a startup error, not a silent downgrade: falling
    back to hashing vectors under a remote model's identity would poison the
    index with vectors that can never be compared to the ones a later run writes.
    """
    settings = (config if config is not None else _memory_config()).get("embedder") or {}
    if not isinstance(settings, dict):
        raise ValueError("memory.embedder must be a mapping")
    kind = str(settings.get("kind", "hashing")).strip().lower()
    policy = str(settings.get("reindex_policy", REINDEX_LAZY)).strip().lower()
    if policy not in (REINDEX_LAZY, REINDEX_STRICT):
        raise ValueError("memory.embedder.reindex_policy must be 'lazy' or 'strict'")

    if kind in ("", "hashing", "local"):
        dimensions = int(settings.get("dimensions", DEFAULT_DIMENSIONS))
        embedder = HashingEmbedder(dimensions)
        if "model_id" in settings and settings["model_id"] != embedder.model.model_id:
            raise ValueError(
                "hashing embedder model_id is derived from its version and dimensions "
                "(%r); configure model_id only for remote embedders" % embedder.model.model_id
            )
        return embedder

    if kind == "remote":
        model_id = str(settings.get("model_id", "")).strip()
        if not model_id:
            raise ValueError("memory.embedder.model_id is required for a remote embedder")
        dimensions = int(settings.get("dimensions", 0))
        if dimensions <= 0:
            raise ValueError("memory.embedder.dimensions is required for a remote embedder")
        key_env = str(settings.get("api_key_env", "") or "").strip()
        api_key = ""
        if key_env:
            # Secrets never live in config.yaml; the config names the env var.
            api_key = os.environ.get(key_env, "")
            if not api_key:
                raise ValueError(
                    "environment variable %s named by memory.embedder.api_key_env is empty" % key_env
                )
        return RemoteEmbedder(
            model_id=model_id,
            dimensions=dimensions,
            base_url=str(settings.get("base_url", "")).strip(),
            api_key=api_key,
            normalize=bool(settings.get("normalize", True)),
            timeout=float(settings.get("timeout", 20.0)),
            reindex_policy=policy,
        )

    raise ValueError("unknown memory.embedder.kind: %r" % kind)


def register(ctx: Any) -> None:
    """Register the coordinator-owned provider, not an independent writer."""
    coordinator = FederatedMemoryCoordinator(embedder=_resolve_embedder())
    # Keep the coordinator alive: it owns the canonical SQLite connection and
    # durable projection recovery for the lifetime of the plugin context.
    setattr(ctx, "_haos_memory_coordinator", coordinator)
    ctx.register_memory_provider(coordinator.memory_provider)
