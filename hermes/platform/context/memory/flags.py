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

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

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

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _validate_name(name: str) -> None:
        if name not in CUTOVER_ORDER:
            raise FlagError("unknown memory feature flag: %r" % name)
