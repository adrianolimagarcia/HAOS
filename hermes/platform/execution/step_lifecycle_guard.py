"""Step Lifecycle Guard for HAOS and Hermes Agent.

Inspired by DeepSeek Harness (DSH) step-level execution lifecycle:
Ensures each tool execution follows before_step -> execute_step -> after_step
and aborts cascade failures when deterministic errors occur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger("haos.step_guard")


@dataclass
class StepContext:
    tool_name: str
    tool_args: Dict[str, Any]
    step_index: int
    total_steps: int
    task_id: Optional[str] = None


@dataclass
class StepVerdict:
    proceed: bool
    abort_remaining: bool = False
    reason: Optional[str] = None
    reflexion_prompt: Optional[str] = None


class StepLifecycleGuard:
    """Guards individual tool execution steps against error cascades."""

    CRITICAL_TOOLS = {"terminal", "patch", "write_file", "execute_code"}

    @classmethod
    def before_step(cls, ctx: StepContext) -> StepVerdict:
        """Pre-execution validation before running a tool step."""
        if not ctx.tool_name:
            return StepVerdict(proceed=False, abort_remaining=True, reason="Empty tool name")
        return StepVerdict(proceed=True)

    @classmethod
    def after_step(cls, ctx: StepContext, result: Any) -> StepVerdict:
        """Post-execution inspection to detect deterministic failures and prevent cascades."""
        if ctx.tool_name not in cls.CRITICAL_TOOLS:
            return StepVerdict(proceed=True)

        res_str = str(result) if result is not None else ""

        # Terminal non-zero exit check
        if ctx.tool_name == "terminal":
            if isinstance(result, dict):
                exit_code = result.get("exit_code")
                if exit_code is not None and exit_code != 0:
                    err_msg = result.get("output", "") or result.get("error", "")
                    return StepVerdict(
                        proceed=False,
                        abort_remaining=True,
                        reason=f"Terminal command failed with exit code {exit_code}",
                        reflexion_prompt=(
                            f"Step {ctx.step_index}/{ctx.total_steps} (terminal) failed with exit code {exit_code}. "
                            f"Error trace: {err_msg[:300]}. Stop cascading tools and diagnose the root cause."
                        ),
                    )

        # Patch or write_file syntax or patch failure
        if ctx.tool_name in ("patch", "write_file"):
            if "error" in res_str.lower() and ("failed" in res_str.lower() or "not found" in res_str.lower()):
                return StepVerdict(
                    proceed=False,
                    abort_remaining=True,
                    reason=f"File operation failed in {ctx.tool_name}",
                    reflexion_prompt=(
                        f"Step {ctx.step_index}/{ctx.total_steps} ({ctx.tool_name}) failed: {res_str[:300]}. "
                        "Do not run subsequent file edits on broken targets; re-read the file first."
                    ),
                )

        return StepVerdict(proceed=True)
