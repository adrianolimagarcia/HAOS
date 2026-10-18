"""Per-agent iteration budget — thread-safe consume/refund counter.

Each ``AIAgent`` (parent or subagent) holds its own :class:`IterationBudget`: the parent's
cap is ``max_iterations`` (default 500), each subagent's ``delegation.max_iterations``
(default 50), so total iterations across parent + subagents can exceed the parent's cap.
"""

from __future__ import annotations

import math
import threading


def normalize_budget_warning_ratio(value) -> float | None:
    """A finite ratio strictly between zero and one, or None (feature off)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    return ratio if math.isfinite(ratio) and 0 < ratio < 1 else None


# Cap on refunds per budget lifetime (M7). The budget is rebuilt every turn
# (turn_context), so this is effectively per-turn: a pure-``execute_code`` loop
# stops getting free iterations after this many and starts consuming the real
# budget, which guarantees termination even when ``max_total`` is unlimited.
MAX_REFUNDS_PER_TURN = 10


class IterationBudget:
    """Thread-safe iteration counter; ``execute_code`` (programmatic tool calling)
    iterations are refunded via :meth:`refund` so they don't eat into the budget —
    up to ``max_refunds`` per turn, after which they consume normally."""

    def __init__(self, max_total: int, max_refunds: int = MAX_REFUNDS_PER_TURN):
        self.max_total = max_total
        self.max_refunds = max_refunds
        self._used = 0
        self._refunds = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Try to consume one iteration.  Returns True if allowed."""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> bool:
        """Give back one iteration (e.g. for execute_code turns).

        Returns True when the refund was applied; False once the per-turn cap
        is reached — the iteration then counts against the budget normally, so
        a refund-only loop always terminates.
        """
        with self._lock:
            if self._used > 0 and self._refunds < self.max_refunds:
                self._used -= 1
                self._refunds += 1
                return True
            return False

    @property
    def refunds_used(self) -> int:
        with self._lock:
            return self._refunds

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)


__all__ = ["IterationBudget", "MAX_REFUNDS_PER_TURN"]
