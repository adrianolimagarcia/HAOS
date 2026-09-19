"""Ordered feature flags for the Memory Fabric cutover.

A cutover that cannot be reversed in one step is not a cutover, it is a rewrite.
These flags encode the migration as an ordered sequence where each stage is
independently reversible, and the ordering is enforced rather than documented:

* **Prerequisites.** ``canonical_fts`` cannot be enabled before
  ``canonical_writes``: serving reads from a journal nothing writes to is a
  guaranteed empty recall, not a partial rollout.
* **Cascade rollback.** Disabling a stage also disables everything that depends
  on it. Rolling back ``canonical_writes`` while ``canonical_fts`` stayed on
  would leave the fabric reading an abandoned journal — the failure mode this
  ordering exists to prevent.
* **Explicit OFF state.** Every flag's disabled state is a real, working code
  path (the pre-cutover behaviour), not a no-op. A flag whose OFF state does
  nothing cannot be rolled back.

The sequence, in order:

1. ``canonical_writes`` — writes land in the canonical journal.
2. ``projections_via_outbox`` — projections are applied by the durable outbox.
3. ``canonical_fts`` — reads are served from the canonical journal.
4. ``vector_rrf`` — the vector channel joins FTS through RRF.
5. ``legacy_writers_disabled`` — the legacy direct-write path is refused.
6. ``legacy_readers_disabled`` — the legacy read path is refused.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# Operator-facing names for each stage. The canonical spelling is the stage name upper-cased;
# the extra entries are the ones the origin branch shipped for the same stage, kept so an
# existing HAOS_MEMORY_* environment keeps meaning what it meant. A HAOS_MEMORY_* name with no
# stage here is warned about, not fatal: the prefix is shared with future variables, so refusing
# to start the fabric over one would be worse than saying it was ignored.
_ENV_ALIASES: Dict[str, str] = {
    "HAOS_MEMORY_CANONICAL_WRITES": "canonical_writes",
    "HAOS_MEMORY_PROJECTIONS_VIA_OUTBOX": "projections_via_outbox",
    "HAOS_MEMORY_DURABLE_PROJECTIONS": "projections_via_outbox",
    "HAOS_MEMORY_CANONICAL_FTS": "canonical_fts",
    "HAOS_MEMORY_CANONICAL_READS": "canonical_fts",
    "HAOS_MEMORY_VECTOR_RRF": "vector_rrf",
    "HAOS_MEMORY_HYBRID_RETRIEVAL": "vector_rrf",
    "HAOS_MEMORY_VECTOR_RETRIEVAL": "vector_rrf",
    "HAOS_MEMORY_LEGACY_WRITERS_DISABLED": "legacy_writers_disabled",
    "HAOS_MEMORY_LEGACY_READERS_DISABLED": "legacy_readers_disabled",
}

_TRUTHY = frozenset({"1", "true", "yes"})
_FALSY = frozenset({"0", "false", "no"})


def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    raise FlagError("%s=%r is not a boolean (use 1/0, true/false or yes/no)" % (name, raw))


def _load_cutover_config() -> Dict[str, Any]:
    """``memory.fabric.cutover`` from the user config, or ``{}`` when unset.

    The effective loader, not the defaults-merged one: this is a presence-sensitive read, and a
    loader that injects ``DEFAULT_CONFIG`` cannot distinguish "the operator did not mention this
    stage" (default: on) from "the operator set it to the default value".

    A broken config yields ``{}`` — every stage stays at its default — because failing a cutover
    read closed would silently roll the fabric back on a YAML typo.
    """
    try:
        from hermes_cli.config_effective import load_user_config_effective

        config = load_user_config_effective() or {}
    except Exception as exc:  # noqa: BLE001 - a config read must never take the fabric down
        logger.warning("memory cutover config unreadable, using defaults: %s", exc)
        return {}
    cutover = ((config.get("memory") or {}).get("fabric") or {}).get("cutover")
    if cutover is None:
        return {}
    if not isinstance(cutover, Mapping):
        raise FlagError("memory.fabric.cutover must be a mapping of stage -> bool, got %r" % type(cutover).__name__)
    return dict(cutover)

# Cutover order. Position in this tuple IS the dependency: each flag may only be
# enabled once every earlier flag is enabled.
CUTOVER_ORDER: Tuple[str, ...] = (
    "canonical_writes",
    "projections_via_outbox",
    "canonical_fts",
    "vector_rrf",
    "legacy_writers_disabled",
    "legacy_readers_disabled",
)

DEFAULT_ENABLED = frozenset(CUTOVER_ORDER)


class FlagError(ValueError):
    """Raised when a flag transition would leave the fabric inconsistent."""


@dataclass
class MemoryFeatureFlags:
    """Mutable cutover state with prerequisite and cascade enforcement."""

    enabled_flags: set = field(default_factory=lambda: set(DEFAULT_ENABLED))

    # -- reads ---------------------------------------------------------------

    def enabled(self, name: str) -> bool:
        self._validate_name(name)
        return name in self.enabled_flags

    def snapshot(self) -> Dict[str, bool]:
        return {name: name in self.enabled_flags for name in CUTOVER_ORDER}

    def active(self) -> List[str]:
        """Enabled flags, in cutover order."""
        return [name for name in CUTOVER_ORDER if name in self.enabled_flags]

    def position(self) -> int:
        """How far the cutover has progressed (index of the last enabled stage)."""
        active = self.active()
        return CUTOVER_ORDER.index(active[-1]) + 1 if active else 0

    # -- writes --------------------------------------------------------------

    def enable(self, name: str) -> None:
        """Enable one stage, requiring every earlier stage to be enabled."""
        self._validate_name(name)
        index = CUTOVER_ORDER.index(name)
        missing = [earlier for earlier in CUTOVER_ORDER[:index] if earlier not in self.enabled_flags]
        if missing:
            raise FlagError("cannot enable %r before %s" % (name, ", ".join(missing)))
        self.enabled_flags.add(name)

    def disable(self, name: str) -> List[str]:
        """Disable one stage and cascade to its dependents. Returns what changed."""
        self._validate_name(name)
        index = CUTOVER_ORDER.index(name)
        cascaded = [later for later in CUTOVER_ORDER[index:] if later in self.enabled_flags]
        for flag in cascaded:
            self.enabled_flags.discard(flag)
        return cascaded

    def advance(self, steps: int = 1) -> List[str]:
        """Enable the next ``steps`` stages in order; returns the ones enabled."""
        turned_on: List[str] = []
        for name in CUTOVER_ORDER:
            if len(turned_on) >= steps:
                break
            if name not in self.enabled_flags:
                self.enable(name)
                turned_on.append(name)
        return turned_on

    def rollback(self, steps: int = 1) -> List[str]:
        """Disable the most recently enabled ``steps`` stages, newest first.

        Cascade means one ``disable`` can remove several flags; the loop keeps
        going until ``steps`` flags are actually off, so ``rollback(2)`` from the
        fully-enabled state removes exactly two stages.
        """
        turned_off: List[str] = []
        for name in reversed(CUTOVER_ORDER):
            if len(turned_off) >= steps:
                break
            if name in self.enabled_flags:
                turned_off.extend(self.disable(name))
        return turned_off

    def reset(self, *, enabled: bool) -> None:
        self.enabled_flags = set(CUTOVER_ORDER) if enabled else set()

    # -- operator control ----------------------------------------------------

    @classmethod
    def from_config(cls, config: Optional[Mapping[str, Any]] = None) -> "MemoryFeatureFlags":
        """The cutover state an operator asked for, from config then ``HAOS_MEMORY_*``.

        Without this the cutover has transitions but no way to reach them: ``enable``/``rollback``
        are only callable from code, so "roll back stage 5 on the appliance" means editing and
        redeploying the tree — which is not a rollback, it is a release.

        Stages are a prefix, not a set: the config is read in ``CUTOVER_ORDER`` and the first
        disabled stage turns off everything after it. Naming a later stage as on while an earlier
        one is off is refused rather than reconciled by the cascade — guessing which side the
        operator meant is how a half-applied cutover happens, and the cascade would have ignored
        the later ``true`` without saying so.
        """
        requested: Dict[str, Any] = dict(config) if config is not None else _load_cutover_config()
        for env_name, stage in _ENV_ALIASES.items():
            raw = os.getenv(env_name)
            if raw is not None:
                requested[stage] = _parse_bool(env_name, raw)
        for env_name in os.environ:
            if env_name.startswith("HAOS_MEMORY_") and env_name not in _ENV_ALIASES:
                logger.warning(
                    "%s names no cutover stage in this build and is ignored (known: %s)",
                    env_name,
                    ", ".join(sorted(_ENV_ALIASES)),
                )

        unknown = sorted(name for name in requested if name not in CUTOVER_ORDER)
        if unknown:
            raise FlagError(
                "unknown memory feature flag(s): %s (known: %s)"
                % (", ".join(unknown), ", ".join(CUTOVER_ORDER))
            )

        # Only an EXPLICIT true after the boundary is incoherent. An unset stage is not an
        # opinion, so it follows the cascade instead of falling back to its default — otherwise
        # disabling one stage would demand listing every later one as false.
        off = [name for name in CUTOVER_ORDER if requested.get(name) is False]
        if off:
            first_off = CUTOVER_ORDER.index(off[0])
            trailing_on = [
                name
                for name in CUTOVER_ORDER
                if requested.get(name) is True and CUTOVER_ORDER.index(name) > first_off
            ]
            if trailing_on:
                raise FlagError(
                    "cutover stages are a prefix: %s cannot be on while %s is off"
                    % (", ".join(trailing_on), off[0])
                )

        flags = cls(enabled_flags=set())
        for name in CUTOVER_ORDER:
            if requested.get(name) is False:
                break
            flags.enable(name)
        return flags

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _validate_name(name: str) -> None:
        if name not in CUTOVER_ORDER:
            raise FlagError("unknown memory feature flag: %r" % name)
