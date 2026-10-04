"""Feature flags and gradual rollout controls for HAOS Civilization multi-agent architecture.

Enables safe migration across stages:
  shadow -> opt-in -> canary -> default-on -> full enforcement

Environment variables:
  HAOS_CIV_ENABLED: Master switch ("1"/"true" vs "0"/"false"). Default: "1"
  HAOS_CIV_CANARY_BOTS: Comma-separated list of bot IDs allowed for full civilization mode.
                       If empty, applies globally to all bots.
  HAOS_CIV_COUNCIL_ENABLED: "1"/"0". Default: "1"
  HAOS_CIV_SOCIETY_ENABLED: "1"/"0". Default: "1"
  HAOS_CIV_EVOLUTION_ENABLED: "1"/"0". Default: "1"
  HAOS_CIV_CONSTITUTION_ENABLED: "1"/"0". Default: "1"
  HAOS_CIV_RECOVERY: "1"/"0". ADR-021 failure-recovery taxonomy (insufficient
                       revalidation + recovery suggestions). Default: "1"
  HAOS_CIV_NATIVE_SIMD: "1"/"0". Force enable/disable native Rust SIMD bridge.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Set


def _env_bool(var_name: str, default: bool = True) -> bool:
    val = os.environ.get(var_name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class CivFeatureFlags:
    """Evaluates runtime feature flags for civilization subsystems and canary bot targeting."""

    civ_enabled: bool = field(default_factory=lambda: _env_bool("HAOS_CIV_ENABLED", True))
    council_enabled: bool = field(default_factory=lambda: _env_bool("HAOS_CIV_COUNCIL_ENABLED", True))
    society_enabled: bool = field(default_factory=lambda: _env_bool("HAOS_CIV_SOCIETY_ENABLED", True))
    evolution_enabled: bool = field(default_factory=lambda: _env_bool("HAOS_CIV_EVOLUTION_ENABLED", True))
    constitution_enabled: bool = field(default_factory=lambda: _env_bool("HAOS_CIV_CONSTITUTION_ENABLED", True))
    recovery_enabled: bool = field(default_factory=lambda: _env_bool("HAOS_CIV_RECOVERY", True))
    canary_bots: Set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        raw_canary = os.environ.get("HAOS_CIV_CANARY_BOTS", "")
        if raw_canary.strip():
            self.canary_bots = {b.strip() for b in raw_canary.split(",") if b.strip()}

    def is_bot_eligible(self, bot_id: Optional[str]) -> bool:
        """Check whether a specific bot is allowed in civilization mode.
        
        If canary_bots is specified, only listed bots are eligible.
        If canary_bots is empty, all bots are eligible (provided civ_enabled is True).
        """
        if not self.civ_enabled:
            return False
        if not self.canary_bots:
            return True
        if not bot_id:
            return False
        return bot_id in self.canary_bots

    def is_feature_enabled(self, feature_name: str, bot_id: Optional[str] = None) -> bool:
        """Check if a specific sub-feature is active, respecting master flag and canary gating."""
        if not self.is_bot_eligible(bot_id):
            return False

        feature = feature_name.lower().strip()
        if feature in ("identity", "leaf", "leaf_snapshot", "events"):
            return self.civ_enabled
        elif feature in ("council", "deliberation"):
            return self.council_enabled
        elif feature in ("society", "reputation", "relationship"):
            return self.society_enabled
        elif feature in ("evolution", "experience", "proposal"):
            return self.evolution_enabled
        elif feature in ("constitution", "policy", "guardrails"):
            return self.constitution_enabled
        elif feature in ("recovery", "failure-handling"):
            # ADR-021: failure-recovery taxonomy (suggestion + revalidation).
            return self.recovery_enabled
        else:
            return self.civ_enabled

    def summary(self) -> dict:
        return {
            "civ_enabled": self.civ_enabled,
            "council_enabled": self.council_enabled,
            "society_enabled": self.society_enabled,
            "evolution_enabled": self.evolution_enabled,
            "constitution_enabled": self.constitution_enabled,
            "recovery_enabled": self.recovery_enabled,
            "canary_mode": bool(self.canary_bots),
            "canary_bots": sorted(self.canary_bots),
        }


# Global default instance
_FLAGS_INSTANCE: Optional[CivFeatureFlags] = None


def get_feature_flags() -> CivFeatureFlags:
    """Retrieve or initialize the active CivFeatureFlags instance."""
    global _FLAGS_INSTANCE
    if _FLAGS_INSTANCE is None:
        _FLAGS_INSTANCE = CivFeatureFlags()
    return _FLAGS_INSTANCE


def reset_feature_flags() -> None:
    """Reset the singleton instance (useful for unit tests modifying env vars)."""
    global _FLAGS_INSTANCE
    _FLAGS_INSTANCE = None
