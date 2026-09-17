"""Explicit rollout controls for Memory Fabric cutover."""
from __future__ import annotations
import os
from dataclasses import dataclass

def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    if raw.lower() not in {"0", "1", "false", "true", "no", "yes"}:
        raise ValueError("invalid boolean %s=%r" % (name, raw))
    return raw.lower() in {"1", "true", "yes"}

@dataclass(frozen=True)
class MemoryFabricFlags:
    canonical_writes: bool = True
    durable_projections: bool = True
    canonical_reads: bool = True
    hybrid_retrieval: bool = True
    vector_retrieval: bool = False
    shadow_retrieval: bool = False

    @classmethod
    def from_env(cls) -> "MemoryFabricFlags":
        return cls(
            canonical_writes=_flag("HAOS_MEMORY_CANONICAL_WRITES", True),
            durable_projections=_flag("HAOS_MEMORY_DURABLE_PROJECTIONS", True),
            canonical_reads=_flag("HAOS_MEMORY_CANONICAL_READS", True),
            hybrid_retrieval=_flag("HAOS_MEMORY_HYBRID_RETRIEVAL", True),
            vector_retrieval=_flag("HAOS_MEMORY_VECTOR_RETRIEVAL", False),
            shadow_retrieval=_flag("HAOS_MEMORY_SHADOW_RETRIEVAL", False),
        )
