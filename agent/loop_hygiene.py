"""Tool repetition hygiene guard for the conversation turn loop.

Ported in spirit from DSH's repeat-tool-reminder:
Detects when a model is issuing identical tool calls repeatedly (same tool name and
identical arguments). When a repetitive pattern is detected, it attaches a stern
reminder to the tool result advising the model to alter its approach or declare a blocker,
preventing infinite loops and token starvation in autonomous tasks (e.g. Kanban workers).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent.loop_hygiene")

# Threshold: number of consecutive identical calls before injecting reminder
DEFAULT_REPEAT_TOOL_THRESHOLD = 2
# Max repetition allowed before forcing an explicit stop warning
MAX_REPEAT_TOOL_STRIKES = 4

DEFAULT_PATCH_FAILURE_THRESHOLD = 4
DEFAULT_TEST_FAILURE_THRESHOLD = 3


@dataclass
class ToolInvocationSignature:
    tool_name: str
    args_hash: str
    raw_preview: str


class RepeatToolGuard:
    """Tracks tool invocation signatures per agent run to detect repetition loops."""

    def __init__(self, threshold: int = DEFAULT_REPEAT_TOOL_THRESHOLD, max_strikes: int = MAX_REPEAT_TOOL_STRIKES):
        self.threshold = threshold
        self.max_strikes = max_strikes
        self._history: List[ToolInvocationSignature] = []
        self._consecutive_count: int = 0
        self._last_signature: Optional[ToolInvocationSignature] = None

    @staticmethod
    def compute_signature(name: str, args: Any) -> ToolInvocationSignature:
        """Compute stable signature for tool call."""
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
                canonical_args = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
            except Exception:
                canonical_args = args.strip()
        elif isinstance(args, dict):
            canonical_args = json.dumps(args, sort_keys=True, ensure_ascii=False)
        else:
            canonical_args = str(args)

        h = hashlib.sha256(canonical_args.encode("utf-8")).hexdigest()[:16]
        preview = canonical_args[:100] + ("..." if len(canonical_args) > 100 else "")
        return ToolInvocationSignature(tool_name=name, args_hash=h, raw_preview=preview)

    def record_and_check(self, name: str, args: Any) -> tuple[bool, Optional[str]]:
        """Record a tool invocation.

        Returns (is_repetition, reminder_message_or_none).
        """
        sig = self.compute_signature(name, args)

        if self._last_signature is not None and (
            self._last_signature.tool_name == sig.tool_name
            and self._last_signature.args_hash == sig.args_hash
        ):
            self._consecutive_count += 1
        else:
            self._consecutive_count = 1
            self._last_signature = sig

        self._history.append(sig)
        if len(self._history) > 50:
            self._history.pop(0)

        if self._consecutive_count >= self.max_strikes:
            msg = (
                f"\n\n[LOOP GUARD CRITICAL]: You have called tool '{sig.tool_name}' with the EXACT same arguments "
                f"{self._consecutive_count} times in a row without making progress. "
                "You MUST STOP repeating this call immediately. If you are blocked or encountering persistent errors, "
                "diagnose the underlying cause, attempt an alternative strategy, or report the obstacle to the user."
            )
            return True, msg
        elif self._consecutive_count >= self.threshold:
            msg = (
                f"\n\n[LOOP GUARD WARNING]: Consecutive identical call #{self._consecutive_count} to '{sig.tool_name}'. "
                "Repeating the same call with identical arguments rarely produces different results. "
                "Consider altering parameters, verifying file contents, or switching strategies."
            )
            return True, msg

        return False, None


class TargetFailureStreakGuard:
    """Tracks target-level consecutive failures (e.g. repeated patch failures on the same file,
    or repeated test command failures in terminal) to detect semantic loops where the model
    slightly varies arguments but keeps failing on the exact same target.
    """

    def __init__(
        self,
        patch_threshold: int = DEFAULT_PATCH_FAILURE_THRESHOLD,
        test_threshold: int = DEFAULT_TEST_FAILURE_THRESHOLD,
    ):
        self.patch_threshold = patch_threshold
        self.test_threshold = test_threshold
        self._file_patch_failures: Dict[str, int] = {}
        self._test_command_failures: Dict[str, int] = {}

    def _extract_path(self, args: Any) -> Optional[str]:
        if isinstance(args, dict):
            return args.get("path") or args.get("file_path") or args.get("file")
        if isinstance(args, str):
            try:
                data = json.loads(args)
                if isinstance(data, dict):
                    return data.get("path") or data.get("file_path") or data.get("file")
            except Exception:
                pass
        return None

    def _extract_command(self, args: Any) -> Optional[str]:
        if isinstance(args, dict):
            return args.get("command") or args.get("cmd")
        if isinstance(args, str):
            try:
                data = json.loads(args)
                if isinstance(data, dict):
                    return data.get("command") or data.get("cmd")
            except Exception:
                return args.strip()
        return None

    def record_and_check(
        self, tool_name: str, args: Any, result_content: str
    ) -> tuple[bool, Optional[str]]:
        """Record target outcome and check for streak breaches.

        Returns (is_breach, reminder_message_or_none).
        """
        res_str = str(result_content)

        if tool_name == "patch":
            path = self._extract_path(args)
            if not path:
                return False, None

            # Detect failure vs success in patch result
            is_failure = (
                "error:" in res_str.lower()
                or "could not find" in res_str.lower()
                or "failed" in res_str.lower()
                or not ("success" in res_str.lower() or "patched" in res_str.lower())
            )

            if is_failure:
                self._file_patch_failures[path] = self._file_patch_failures.get(path, 0) + 1
                count = self._file_patch_failures[path]
                if count >= self.patch_threshold:
                    msg = (
                        f"\n\n[TARGET FAILURE STREAK]: You have failed to patch '{path}' "
                        f"{count} times in a row with different search strings. "
                        "STOP guessing search strings! Read the actual file with `read_file` to see its exact lines, "
                        "indentation, and surrounding context, or use `write_file` if rewriting cleanly."
                    )
                    return True, msg
            else:
                self._file_patch_failures[path] = 0

            return False, None

        if tool_name in ("terminal", "bash"):
            cmd = self._extract_command(args)
            if not cmd:
                return False, None

            # Detect test failure
            is_failure = (
                "FAILED" in res_str
                or "exit code 1" in res_str
                or "exit code 2" in res_str
                or "AssertionError" in res_str
                or "Error:" in res_str
            )

            if is_failure:
                self._test_command_failures[cmd] = self._test_command_failures.get(cmd, 0) + 1
                count = self._test_command_failures[cmd]
                if count >= self.test_threshold:
                    msg = (
                        f"\n\n[TEST FAILURE STREAK]: Test command '{cmd}' has failed {count} times consecutively. "
                        "STOP repeatedly running failing tests without an effective fix. "
                        "Inspect the specific failure trace, check recent edits, or revert invalid hypotheses."
                    )
                    return True, msg
            else:
                self._test_command_failures[cmd] = 0

            return False, None

        return False, None


def attach_repetition_reminder_if_needed(agent: Any, tool_name: str, args: Any, tool_result_content: str) -> str:
    """Helper used during tool execution to append loop hygiene reminder if needed."""
    guard = getattr(agent, "_repeat_tool_guard", None)
    if guard is None:
        guard = RepeatToolGuard()
        setattr(agent, "_repeat_tool_guard", guard)

    is_rep, reminder = guard.record_and_check(tool_name, args)
    if is_rep and reminder:
        logger.warning(
            "Repeat tool invocation detected for '%s' (repetition count=%d)",
            tool_name, guard._consecutive_count
        )
        return tool_result_content + reminder

    return tool_result_content


def attach_target_failure_reminder_if_needed(agent: Any, tool_name: str, args: Any, tool_result_content: str) -> str:
    """Helper used during tool execution to append target failure streak reminder if needed."""
    guard = getattr(agent, "_target_failure_guard", None)
    if guard is None:
        guard = TargetFailureStreakGuard()
        setattr(agent, "_target_failure_guard", guard)

    is_breach, reminder = guard.record_and_check(tool_name, args, tool_result_content)
    if is_breach and reminder:
        logger.warning(
            "Target failure streak detected for '%s'",
            tool_name
        )
        return tool_result_content + reminder

    return tool_result_content