"""Best-effort structured checkpoint before native HAOS conversation compaction.

Checkpoints supplement durable transcripts; they never mutate LLM messages or
provider prompt prefixes. They are evidence pointers, not promoted memories.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

_TEST_LINE = re.compile(r"\b(?:passed|failed|errors?|traceback|assertionerror|fatal)\b", re.I)
_PATH_KEY = frozenset({"path", "file", "file_path", "filename", "target_path", "cwd"})


def capture_compaction_checkpoint(agent: Any, messages: list[dict[str, Any]],
                                  task_id: str = "") -> bool:
    """Return False (without mutating the transcript) when disabled or unavailable."""
    if os.environ.get("HAOS_CONTEXT_RETRIEVAL_ENABLED", "").lower() not in ("1", "true", "yes"):
        return False
    session_id = str(getattr(agent, "session_id", "") or "")
    if not session_id:
        return False

    from agent.redact import redact_sensitive_text
    from hermes.platform.context.budget.artifact_index import ContextArtifactIndex

    def scrub(value: Any, limit: int) -> str:
        text = str(value or "")[:limit]
        return redact_sensitive_text(text, force=True, redact_url_credentials=True)

    paths: set[str] = set()
    tool_names: set[str] = set()
    observations: list[str] = []
    last_user = ""
    for message in messages[-60:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        raw_content = message.get("content")
        if role == "user" and isinstance(raw_content, str):
            last_user = scrub(raw_content, 900)
        if role == "tool" and isinstance(raw_content, str):
            for line in raw_content.splitlines()[:80]:
                if _TEST_LINE.search(line):
                    observations.append(scrub(line, 240))
                    if len(observations) >= 18:
                        break
        if role == "assistant":
            for call in (message.get("tool_calls") or [])[:12]:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                if not isinstance(fn, dict):
                    continue
                name = str(fn.get("name") or "")
                if name:
                    tool_names.add(name[:80])
                try:
                    params = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    params = {}
                if isinstance(params, dict):
                    for key in _PATH_KEY:
                        value = params.get(key)
                        if isinstance(value, str) and len(value) <= 400:
                            paths.add(scrub(value, 400))
    checkpoint = {
        "task_id": scrub(task_id, 200),
        "last_user_request": last_user,
        "tools": sorted(tool_names)[:30],
        "paths": sorted(paths)[:30],
        "test_errors_and_status": observations[-18:],
        "kind": "auto_before_compaction",
    }
    ContextArtifactIndex().save_checkpoint(session_id, checkpoint)
    return True
