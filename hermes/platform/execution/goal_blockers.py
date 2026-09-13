"""HAOS Goal Blocker Typing & Evaluation Engine.

Inspired by ByteDance DeerFlow 2.0 SuperAgent Harness:
Typed blockers classify execution halts into machine-actionable categories:
- missing_evidence: Test output, artifacts, or verifiable metrics are missing.
- needs_user_input: Ambiguity, credentials, or explicit approval required from human.
- run_failed: Non-zero exit code, unhandled exception, syntax error, or crash.
- external_wait: Waiting on external async events, webhook, CI/CD run, or lock.
- goal_not_met_yet: Normal in-flight state; execution still in progress.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
import re
from typing import Any, Dict, List, Optional


class BlockerKind(str, Enum):
    MISSING_EVIDENCE = "missing_evidence"
    NEEDS_USER_INPUT = "needs_user_input"
    RUN_FAILED = "run_failed"
    EXTERNAL_WAIT = "external_wait"
    GOAL_NOT_MET_YET = "goal_not_met_yet"


@dataclass
class GoalEvaluation:
    satisfied: bool
    blocker_kind: Optional[BlockerKind]
    reason: str
    evidence: List[str] = field(default_factory=list)
    should_continue: bool = False
    continuation_prompt: Optional[str] = None

    @property
    def formatted_reason(self) -> str:
        if self.satisfied:
            return "Goal satisfied successfully."
        prefix = f"[{self.blocker_kind.value.upper()}]" if self.blocker_kind else "[BLOCKED]"
        ev_str = f" (Evidence: {', '.join(self.evidence)})" if self.evidence else ""
        return f"{prefix} {self.reason}{ev_str}"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.blocker_kind:
            data["blocker_kind"] = self.blocker_kind.value
        data["formatted_reason"] = self.formatted_reason
        return data


class GoalBlockerEvaluator:
    """Deterministic evaluator for goal progression and blocker categorization."""

    @staticmethod
    def classify_blocker_reason(reason_text: str) -> BlockerKind:
        """Classify free-form text or structured prefix into a standard BlockerKind."""
        lower = (reason_text or "").lower()

        # Check explicit tags first
        if "missing_evidence" in lower or "[missing_evidence]" in lower:
            return BlockerKind.MISSING_EVIDENCE
        if "needs_user_input" in lower or "[needs_user_input]" in lower or "user_input" in lower:
            return BlockerKind.NEEDS_USER_INPUT
        if "run_failed" in lower or "[run_failed]" in lower:
            return BlockerKind.RUN_FAILED
        if "external_wait" in lower or "[external_wait]" in lower or "waiting" in lower:
            return BlockerKind.EXTERNAL_WAIT
        if "goal_not_met_yet" in lower or "[goal_not_met_yet]" in lower:
            return BlockerKind.GOAL_NOT_MET_YET

        # Semantic keywords
        if any(w in lower for w in ("missing test", "no proof", "no assertion", "unverified", "missing evidence", "evidence")):
            return BlockerKind.MISSING_EVIDENCE
        if any(w in lower for w in ("user", "human", "approval", "confirm", "confirmation", "clarification", "password", "input")):
            return BlockerKind.NEEDS_USER_INPUT
        if any(w in lower for w in ("exit code", "exception", "traceback", "crash", "failed to run", "compilation error", "syntax error", "fail", "failed")):
            return BlockerKind.RUN_FAILED
        if any(w in lower for w in ("webhook", "ci/cd", "timeout", "lock acquired", "external api", "rate limit", "waiting", "wait")):
            return BlockerKind.EXTERNAL_WAIT

        return BlockerKind.GOAL_NOT_MET_YET

    @classmethod
    def evaluate_state(
        cls,
        *,
        goal: str,
        turn_count: int = 1,
        max_turns: int = 8,
        exit_code: Optional[int] = None,
        last_output: Optional[str] = None,
        files_modified: Optional[List[str]] = None,
        tests_executed: bool = False,
        tests_passed: bool = False,
        user_input_requested: bool = False,
    ) -> GoalEvaluation:
        """Evaluates goal state against turn signals."""
        evidence: List[str] = []

        if user_input_requested:
            return GoalEvaluation(
                satisfied=False,
                blocker_kind=BlockerKind.NEEDS_USER_INPUT,
                reason="Agent requested human feedback or confirmation before proceeding.",
                evidence=["Explicit human clarification requested"],
                should_continue=False,
            )

        if exit_code is not None and exit_code != 0:
            evidence.append(f"Exit code: {exit_code}")
            if last_output:
                evidence.append(last_output[:200].strip())
            return GoalEvaluation(
                satisfied=False,
                blocker_kind=BlockerKind.RUN_FAILED,
                reason=f"Execution step failed with exit code {exit_code}.",
                evidence=evidence,
                should_continue=False,
                continuation_prompt="Investigate and fix the failing command.",
            )

        # If files were modified but no test verified them
        if files_modified and not tests_executed:
            evidence.append(f"{len(files_modified)} file(s) modified: {', '.join(files_modified[:3])}")
            evidence.append("No automated test suite execution recorded")
            return GoalEvaluation(
                satisfied=False,
                blocker_kind=BlockerKind.MISSING_EVIDENCE,
                reason="Code modifications made without test suite execution or verification output.",
                evidence=evidence,
                should_continue=True,
                continuation_prompt="Run the automated test suite to provide verification evidence for your changes.",
            )

        if tests_executed and not tests_passed:
            evidence.append("Test suite executed with failures")
            return GoalEvaluation(
                satisfied=False,
                blocker_kind=BlockerKind.RUN_FAILED,
                reason="Test suite failed. Acceptance criteria not fulfilled.",
                evidence=evidence,
                should_continue=True,
                continuation_prompt="Refactor implementation to make all failing tests pass.",
            )

        if tests_executed and tests_passed:
            evidence.append("Test suite passed cleanly")
            return GoalEvaluation(
                satisfied=True,
                blocker_kind=None,
                reason="Goal verified with passing tests.",
                evidence=evidence,
                should_continue=False,
            )

        # Default in-flight progression
        if turn_count < max_turns:
            return GoalEvaluation(
                satisfied=False,
                blocker_kind=BlockerKind.GOAL_NOT_MET_YET,
                reason=f"Goal in progress (Turn {turn_count}/{max_turns}).",
                evidence=[f"Turn {turn_count} of {max_turns}"],
                should_continue=True,
            )

        # Reached round limit without verification
        return GoalEvaluation(
            satisfied=False,
            blocker_kind=BlockerKind.MISSING_EVIDENCE,
            reason=f"Max turn limit ({max_turns}) reached without conclusive verification evidence.",
            evidence=[f"Reached max limit of {max_turns} rounds"],
            should_continue=False,
        )
