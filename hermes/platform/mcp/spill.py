"""MCP Result Budget and Spill-to-file mechanism.

Enforces character budgets on MCP tool execution results:
- Results under budget pass through intact.
- Results exceeding budget are truncated to budget characters.
- Full output is dumped to ~/.haos/mcp-spills/<timestamp>-<tool>.txt.
- Returns truncated content + a short reference marker.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

from hermes_constants import get_hermes_home

DEFAULT_MCP_RESULT_BUDGET = 20_000


def get_mcp_result_budget() -> int:
    """Return the configured character budget for MCP tool results."""
    val = os.environ.get("HAOS_MCP_RESULT_BUDGET")
    if val:
        try:
            return max(100, int(val))
        except (ValueError, TypeError):
            pass
    return DEFAULT_MCP_RESULT_BUDGET


def get_spills_dir() -> Path:
    """Return the directory for MCP spills under HERMES_HOME."""
    spills_dir = Path(get_hermes_home()) / "mcp-spills"
    spills_dir.mkdir(parents=True, exist_ok=True)
    return spills_dir


def sanitize_tool_name(tool_name: str) -> str:
    """Sanitize tool name for use in filenames."""
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", tool_name.strip())
    return cleaned[:64] or "mcp_tool"


def apply_mcp_result_budget(
    text: str,
    tool_name: str = "mcp_tool",
    budget: Optional[int] = None,
    spills_dir: Optional[Path] = None,
) -> str:
    """Apply character budget to an MCP tool result.

    If len(text) <= budget:
        returns text intact.
    If len(text) > budget:
        dumps full content to ~/.haos/mcp-spills/<timestamp>-<tool>.txt
        returns text[:budget] + short reference note.
    """
    if not isinstance(text, str):
        text = str(text)

    effective_budget = budget if budget is not None else get_mcp_result_budget()

    if len(text) <= effective_budget:
        return text

    # Opt-in selective retrieval. Store the ENTIRE output before returning a
    # small excerpt. Never claim success when indexing fails: the old lossless
    # spill-to-file path remains the fallback.
    if os.environ.get("HAOS_CONTEXT_RETRIEVAL_ENABLED", "").lower() in ("1", "true", "yes"):
        try:
            from hermes.platform.context.budget.artifact_index import ContextArtifactIndex
            index = ContextArtifactIndex()
            uri = index.put(
                text, title=tool_name, kind="mcp_output", source=tool_name,
                summary=text[:600], abstract=text[:160],
            )
            # Keep output compact even when the caller configures a high cap.
            excerpt_budget = min(effective_budget, 4000)
            headline = (
                f"[MCP output indexed: {len(text)} chars; uri={uri}]\n"
                f"Use context_search or context_expand for exact full output.\n"
            )
            lines = text.splitlines()
            hot = [line for line in lines if re.search(
                r"(?i)\b(error|failed|failure|traceback|exception|fatal|critical|denied)\b",
                line,
            )][:16]
            if hot:
                snippet = "\n".join(hot)
            else:
                snippet = text[:1200] + ("\n... [middle indexed] ...\n" if len(text) > 2000 else "") + text[-800:]
            return (headline + snippet)[:excerpt_budget]
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "MCP artifact indexing failed; falling back to spill-to-file"
            )

    target_dir = spills_dir or get_spills_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time() * 1000)
    clean_name = sanitize_tool_name(tool_name)
    spill_filename = f"{timestamp}-{clean_name}.txt"
    spill_file = target_dir / spill_filename

    # Write the full output intact to the spill file
    spill_file.write_text(text, encoding="utf-8")

    truncated = text[:effective_budget]
    ref_marker = f"\n\n[truncado; completo em {spill_file.resolve()}]"
    return f"{truncated}{ref_marker}"
