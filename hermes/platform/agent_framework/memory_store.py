"""Operational Memory Store implementing the 'Planning with Files' paradigm.

Manages persistent, human-readable, and LLM-injectable Markdown memory logs
tracking incidents, architectural decisions, hardware facts, optimizations,
and current operational working memory.
"""

from __future__ import annotations

import fcntl
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hermes.platform.agent_framework.state_manager import HAOSStateManager
from hermes_constants import mkdir_under_hermes_home


class OperationalMemoryStore:
    """Markdown-backed operational memory store for HAOS autonomous agents.

    Adheres to the 'Planning with Files' pattern: state, history, and knowledge
    are held in durable markdown files formatted for direct LLM prompt injection
    and operator review.
    """

    DEFAULT_FILES = {
        "incidents.md": "# HAOS Incidents & Diagnostic History\n\nLog of anomalies, root causes, interventions, and resolution outcomes.\n\n",
        "decisions.md": "# HAOS Architectural & Operational Decisions\n\nLog of design and operational decisions, rationale, alternatives, and trade-offs.\n\n",
        "optimizations.md": "# HAOS System Optimizations\n\nRecord of parameter tunings, resource limits, and observed performance impacts.\n\n",
        "hardware.md": "# HAOS Hardware Facts & Topology\n\nAuthoritative inventory of hardware devices, block devices, and sensor topology.\n\n",
        "current.md": "# HAOS Current Operational State\n\nActive operational memory, health status, active tasks, and unresolved issues.\n\n",
    }

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is not None:
            self.base_dir = Path(base_dir).resolve()
        else:
            from hermes_constants import get_hermes_home

            self.base_dir = (Path(get_hermes_home()) / "agent").resolve()

        self.memory_dir = self.base_dir / "memory"
        HAOSStateManager._check_path(self.memory_dir)
        mkdir_under_hermes_home(self.memory_dir)
        self._lock_file = self.memory_dir / ".memory.lock"

        # Initialization must share the append lock or a concurrent append can be lost.
        with self._acquire_lock():
            self._ensure_default_files()

    def _ensure_default_files(self) -> None:
        """Initialize missing memory files with standardized headers."""
        for filename, header in self.DEFAULT_FILES.items():
            filepath = self.memory_dir / filename
            HAOSStateManager._check_path(filepath)
            if not filepath.exists():
                self._write_file_atomic(filepath, header)

    @contextmanager
    def _acquire_lock(self, timeout_sec: float = 5.0) -> Iterator[None]:
        """Exclusive file lock on .memory.lock for concurrent safe memory writes."""
        HAOSStateManager._check_path(self._lock_file)
        fd = os.open(str(self._lock_file), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        start_time = time.monotonic()
        acquired = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError) as exc:
                    if time.monotonic() - start_time >= timeout_sec:
                        raise TimeoutError(f"Timed out acquiring memory lock after {timeout_sec:.2f}s") from exc
                    time.sleep(0.02)
            yield
        finally:
            if acquired:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass

    def _read_file_text(self, path: Path) -> str:
        """Safely read text from a markdown file."""
        HAOSStateManager._check_path(path)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def _write_file_atomic(self, path: Path, content: str) -> None:
        """Atomically replace file content via temporary sibling file."""
        HAOSStateManager._check_path(path)
        mkdir_under_hermes_home(path.parent)
        tmp_path = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex}")
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _append_section(self, filename: str, section_content: str) -> None:
        """Thread-safe and process-safe append of a markdown section."""
        if not HAOSStateManager._safe_name(filename):
            raise ValueError("Memory filename must be a plain basename")
        filepath = self.memory_dir / filename
        with self._acquire_lock():
            existing = self._read_file_text(filepath)
            if not existing.strip():
                existing = self.DEFAULT_FILES.get(filename, f"# {Path(filename).stem}\n\n")

            if not existing.endswith("\n\n"):
                if existing.endswith("\n"):
                    existing += "\n"
                else:
                    existing += "\n\n"

            updated = existing + section_content.strip() + "\n\n"
            self._write_file_atomic(filepath, updated)

    def record_incident(
        self,
        title: str,
        problem: str,
        diagnosis: str,
        actions: list[str],
        result: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Append an incident diagnostic and resolution record to incidents.md.

        Returns:
            Unique incident ID (e.g. `INC-20260401-120000-a1b2`).
        """
        now = datetime.now(timezone.utc)
        iso_now = now.isoformat()
        incident_id = f"INC-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"

        lines = [
            f"## [{incident_id}] {title.strip()}",
            f"- **Timestamp**: `{iso_now}`",
            f"- **Problem**: {problem.strip()}",
            f"- **Diagnosis**: {diagnosis.strip()}",
            "- **Actions Taken**:",
        ]
        if actions:
            for act in actions:
                lines.append(f"  - {act.strip()}")
        else:
            lines.append("  - None")

        lines.append(f"- **Result**: {result.strip()}")

        if metadata:
            lines.append("- **Metadata**:")
            for k, v in metadata.items():
                lines.append(f"  - **{k}**: `{v}`")

        self._append_section("incidents.md", "\n".join(lines))
        return incident_id

    def record_decision(
        self,
        title: str,
        rationale: str,
        alternatives: list[str] | None = None,
        impact: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Append an architectural or operational decision to decisions.md.

        Returns:
            Unique decision ID (e.g. `DEC-20260401-120000-a1b2`).
        """
        now = datetime.now(timezone.utc)
        iso_now = now.isoformat()
        decision_id = f"DEC-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"

        lines = [
            f"## [{decision_id}] {title.strip()}",
            f"- **Timestamp**: `{iso_now}`",
            f"- **Rationale**: {rationale.strip()}",
        ]
        if alternatives:
            lines.append("- **Alternatives Considered**:")
            for alt in alternatives:
                lines.append(f"  - {alt.strip()}")
        if impact.strip():
            lines.append(f"- **Anticipated Impact**: {impact.strip()}")

        if metadata:
            lines.append("- **Metadata**:")
            for k, v in metadata.items():
                lines.append(f"  - **{k}**: `{v}`")

        self._append_section("decisions.md", "\n".join(lines))
        return decision_id

    def record_optimization(
        self,
        component: str,
        parameter: str,
        before: str,
        after: str,
        justification: str,
        observed_impact: str = "",
    ) -> str:
        """Append a parameter tuning or optimization entry to optimizations.md.

        Returns:
            Unique optimization ID (e.g. `OPT-20260401-120000-a1b2`).
        """
        now = datetime.now(timezone.utc)
        iso_now = now.isoformat()
        opt_id = f"OPT-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"

        lines = [
            f"## [{opt_id}] {component.strip()} / {parameter.strip()}",
            f"- **Timestamp**: `{iso_now}`",
            f"- **Component**: `{component.strip()}`",
            f"- **Parameter**: `{parameter.strip()}`",
            f"- **Before**: `{before.strip()}`",
            f"- **After**: `{after.strip()}`",
            f"- **Justification**: {justification.strip()}",
        ]
        if observed_impact.strip():
            lines.append(f"- **Observed Impact**: {observed_impact.strip()}")

        self._append_section("optimizations.md", "\n".join(lines))
        return opt_id

    def update_hardware_fact(self, device_name: str, facts: dict[str, Any]) -> None:
        """Update or insert hardware facts for a specific device in hardware.md."""
        filepath = self.memory_dir / "hardware.md"
        with self._acquire_lock():
            existing = self._read_file_text(filepath)
            if not existing.strip():
                existing = self.DEFAULT_FILES["hardware.md"]

            iso_now = datetime.now(timezone.utc).isoformat()
            device_header = f"### Device: {device_name.strip()}"

            new_block_lines = [
                device_header,
                f"- **Last Updated**: `{iso_now}`",
            ]
            for key, val in facts.items():
                new_block_lines.append(f"- **{key}**: `{val}`")
            new_block = "\n".join(new_block_lines)

            # Check if this device section already exists in the file
            pattern = re.compile(
                r"^### Device:[ \t]*" + re.escape(device_name.strip()) + r"[ \t]*\n.*?(?=(^### Device:|\Z))",
                re.MULTILINE | re.DOTALL,
            )

            if pattern.search(existing):
                updated = pattern.sub(new_block + "\n\n", existing)
            else:
                updated = existing.rstrip() + "\n\n" + new_block + "\n\n"

            self._write_file_atomic(filepath, updated)

    def update_current_summary(
        self,
        summary: str,
        active_tasks: list[str] | None = None,
        issues: list[str] | None = None,
    ) -> None:
        """Rewrite current.md with the latest active operational working memory."""
        filepath = self.memory_dir / "current.md"
        iso_now = datetime.now(timezone.utc).isoformat()

        lines = [
            "# HAOS Current Operational State",
            f"*Last Updated: `{iso_now}`*",
            "",
            "## Health Summary",
            summary.strip(),
            "",
            "## Active Tasks",
        ]
        if active_tasks:
            for task in active_tasks:
                lines.append(f"- {task.strip()}")
        else:
            lines.append("- *No active tasks recorded.*")

        lines.extend([
            "",
            "## Unresolved Issues / Blockers",
        ])
        if issues:
            for issue in issues:
                lines.append(f"- [!] {issue.strip()}")
        else:
            lines.append("- *No active issues or blockers.*")

        lines.append("")
        content = "\n".join(lines)

        with self._acquire_lock():
            self._write_file_atomic(filepath, content)

    def search_memory(self, file_name: str, query: str) -> list[str]:
        """Search a specific memory markdown file for keyword matches.

        Args:
            file_name: Target markdown file (e.g. 'incidents.md', 'decisions').
            query: Keyword or phrase to search (case-insensitive).

        Returns:
            List of matching sections (or matching lines if no section markers exist).
        """
        target_name = file_name if file_name.endswith(".md") else f"{file_name}.md"
        if not HAOSStateManager._safe_name(target_name):
            raise ValueError("Memory filename must be a plain basename")
        filepath = self.memory_dir / target_name
        content = self._read_file_text(filepath)
        if not content.strip() or not query.strip():
            return []

        q_lower = query.lower()
        results: list[str] = []

        # Split content into sections starting with ## or ###
        sections = re.split(r"(?=(?:^|\n)##+ )", content)
        for sec in sections:
            clean_sec = sec.strip()
            if not clean_sec:
                continue
            if q_lower in clean_sec.lower():
                results.append(clean_sec)

        # Fallback to line matching if no sections matched
        if not results:
            for line in content.splitlines():
                if q_lower in line.lower() and line.strip():
                    results.append(line.strip())

        return results

    def get_recent_context(self, max_chars: int = 4000) -> str:
        """Consolidate high-priority memory context for LLM prompt injection.

        Assembles current operational state, latest incidents, and optimizations,
        truncated cleanly to stay under max_chars.
        """
        current_text = self._read_file_text(self.memory_dir / "current.md").strip()
        incidents_text = self._read_file_text(self.memory_dir / "incidents.md").strip()
        optimizations_text = self._read_file_text(self.memory_dir / "optimizations.md").strip()
        decisions_text = self._read_file_text(self.memory_dir / "decisions.md").strip()

        # Extract recent incident entries (up to last 3)
        incident_entries = [e.strip() for e in re.split(r"(?=(?:^|\n)## \[INC-)", incidents_text) if e.strip().startswith("## [INC-")]
        recent_incidents = "\n\n".join(incident_entries[-3:]) if incident_entries else "*No incidents logged.*"

        # Extract recent optimization entries (up to last 3)
        opt_entries = [e.strip() for e in re.split(r"(?=(?:^|\n)## \[OPT-)", optimizations_text) if e.strip().startswith("## [OPT-")]
        recent_opts = "\n\n".join(opt_entries[-3:]) if opt_entries else "*No optimizations logged.*"

        # Extract recent decision entries (up to last 2)
        dec_entries = [e.strip() for e in re.split(r"(?=(?:^|\n)## \[DEC-)", decisions_text) if e.strip().startswith("## [DEC-")]
        recent_decs = "\n\n".join(dec_entries[-2:]) if dec_entries else "*No architectural decisions logged.*"

        parts = [
            "# HAOS Operational Context & Working Memory",
            "",
            "## Current Operational State",
            current_text or "*No current operational state summary available.*",
            "",
            "## Recent Incidents & Diagnoses",
            recent_incidents,
            "",
            "## Recent Parameter Optimizations",
            recent_opts,
            "",
            "## Recent Operational Decisions",
            recent_decs,
        ]

        full_context = "\n".join(parts)
        if len(full_context) <= max_chars:
            return full_context

        # If too long, prioritize Current State and truncate tail gracefully
        header_section = "\n".join(parts[:4])  # Header + current state
        budget_left = max(0, max_chars - len(header_section) - 100)

        tail_content = "\n".join(parts[4:])
        if len(tail_content) > budget_left:
            tail_content = tail_content[:budget_left].rsplit("\n", 1)[0] + "\n\n*[Remaining context truncated for budget]*"

        return f"{header_section}\n\n{tail_content}".strip()
