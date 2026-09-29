"""Deterministic Council MEMORY.md projection engine.

Reconstructs structured, human-readable Council memory files directly from
canonical append-only events with atomic updates, SHA-256 integrity verification,
and operator override protections.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event

logger = logging.getLogger("hermes.platform.council.memory_projection")


@dataclass
class CouncilMemoryRecord:
    council_id: str
    profile_id: str
    last_seq: int
    content_hash: str
    updated_at: float
    file_path: str
    decisions_count: int
    members: List[str]
    has_operator_override: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CouncilMemoryProjectionEngine:
    """Projects canonical council events into auditable, deterministic MEMORY.md files."""

    def __init__(
        self,
        event_store: EventStore,
        output_dir: Optional[Path] = None,
        profile_id: str = "default",
    ):
        self.store = event_store
        self.profile_id = profile_id

        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            try:
                from hermes_constants import get_hermes_home
                self.output_dir = Path(get_hermes_home()) / "councils"
            except ImportError:
                self.output_dir = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "councils"

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir = self.output_dir / ".projection_meta"
        self.meta_dir.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, council_id: str) -> Path:
        return self.meta_dir / f"{council_id}_{self.profile_id}.meta.json"

    def _load_meta(self, council_id: str) -> Optional[Dict[str, Any]]:
        path = self._meta_path(council_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load projection metadata for {council_id}: {e}")
        return None

    def _save_meta(self, council_id: str, meta: Dict[str, Any]) -> None:
        path = self._meta_path(council_id)
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
            tmp.replace(path)
        except Exception as e:
            logger.error(f"Failed to save projection metadata for {council_id}: {e}")

    def render_markdown(
        self,
        council_id: str,
        council_info: Dict[str, Any],
        decisions: List[Dict[str, Any]],
        roles: Dict[str, str],
        relationships: List[Dict[str, Any]],
        operator_section: Optional[str] = None,
    ) -> str:
        """Render deterministic Markdown document from structured canonical state."""
        lines: List[str] = []
        lines.append(f"# Council Memory: {council_id}")
        lines.append("")
        lines.append(f"> **Profile:** `{self.profile_id}` | **Projection Engine:** HAOS Deterministic V1")
        lines.append("")

        # 1. Charter & Composition
        lines.append("## 1. Charter & Composition")
        purpose = council_info.get("purpose", "Autonomous multi-bot governance council.")
        mode = council_info.get("decision_mode", "consensus_with_dissent")
        members = council_info.get("members", [])
        lines.append(f"- **Purpose:** {purpose}")
        lines.append(f"- **Decision Mode:** `{mode}`")
        lines.append("- **Enrolled Members:**")
        if members:
            for m in members:
                assigned_role = roles.get(m, council_info.get("roles", {}).get(m, "Member"))
                lines.append(f"  - `{m}` — *{assigned_role}*")
        else:
            lines.append("  - *(No members registered)*")
        lines.append("")

        # 2. Ratified Decisions & Precedents
        lines.append("## 2. Ratified Decisions & Precedents")
        if decisions:
            for idx, dec in enumerate(decisions, 1):
                dec_id = dec.get("id", f"dec-{idx}")
                lines.append(f"### {idx}. Decision `{dec_id}`")
                lines.append(f"- **Ratified:** {dec.get('timestamp_str', 'N/A')}")
                lines.append(f"- **Resolution:** {dec.get('decision', 'Approved')}")
                lines.append(f"- **Confidence:** {float(dec.get('confidence', 1.0)):.2f}")
                lines.append(f"- **Synthesis:** {dec.get('synthesis', '')}")

                participants = dec.get("participants", [])
                if participants:
                    lines.append(f"- **Participants:** {', '.join(f'`{p}`' for p in participants)}")

                dissent = dec.get("dissent", {})
                if dissent:
                    lines.append("- **Recorded Dissent:**")
                    for bot, diss_text in dissent.items():
                        lines.append(f"  - `{bot}`: {diss_text}")

                action_refs = dec.get("action_refs", [])
                if action_refs:
                    lines.append(f"- **Action Intents:** {', '.join(f'`{a}`' for a in action_refs)}")
                lines.append("")
        else:
            lines.append("*(No decisions recorded yet)*")
            lines.append("")

        # 3. Inter-Agent Relationships & Social Fabric
        lines.append("## 3. Social Fabric & Roles")
        if relationships:
            lines.append("| Source Bot | Relation | Target Bot | Weight |")
            lines.append("|---|---|---|---|")
            for r in relationships:
                lines.append(f"| `{r.get('from_bot')}` | `{r.get('relation_type')}` | `{r.get('to_bot')}` | {r.get('weight', 1.0)} |")
            lines.append("")
        else:
            lines.append("*(No external relationship assertions recorded)*")
            lines.append("")

        # 4. Operator Overrides (if manual edits were detected)
        if operator_section and operator_section.strip():
            lines.append("## 4. Operator Overrides & Manual Annotations")
            lines.append(operator_section.strip())
            lines.append("")

        # 5. Provenance & Integrity Footer
        lines.append("---")
        lines.append("*This file is a deterministic projection of canonical events from the HAOS EventStore.*")
        lines.append(f"*Generated at: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}*")
        lines.append("")

        return "\n".join(lines)

    def project_council(
        self,
        council_id: str,
        force_full_rebuild: bool = False,
    ) -> CouncilMemoryRecord:
        """Compile and project all canonical events for a council into its MEMORY.md file."""
        events: List[Event] = self.store.get_all()

        council_info: Dict[str, Any] = {}
        decisions: List[Dict[str, Any]] = []
        roles: Dict[str, str] = {}
        relationships: List[Dict[str, Any]] = []

        max_seq = 0

        for idx, ev in enumerate(events):
            seq = getattr(ev, "seq", idx + 1)
            if seq > max_seq:
                max_seq = seq

            payload = ev.payload or {}

            # Council registration
            if ev.name in ("civ.council.created", "civ.council.registered"):
                spec_data = payload.get("spec", {})
                if spec_data.get("id") == council_id:
                    council_info.update(spec_data)

            # Council decisions
            elif ev.name in ("civ.council.decision-recorded", "agent_council.decision"):
                rec_data = payload.get("decision") or payload.get("decision_record") or payload.get("record") or payload
                # Check if decision belongs to this council
                if rec_data.get("council_id") == council_id or council_id in rec_data.get("id", ""):
                    t_str = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(ev.timestamp))
                    rec_copy = dict(rec_data)
                    rec_copy["timestamp_str"] = t_str
                    decisions.append(rec_copy)

            # Society roles
            elif ev.name == "civ.society.role-assigned":
                bot_id = payload.get("bot_id")
                role_name = payload.get("role")
                if bot_id and role_name:
                    roles[bot_id] = role_name

            # Society relationships
            elif ev.name == "civ.society.relationship-established":
                rel = payload.get("relationship", {})
                members = council_info.get("members", [])
                if rel.get("from_bot") in members or rel.get("to_bot") in members:
                    relationships.append(rel)

        # Target file directory
        council_dir = self.output_dir / council_id
        council_dir.mkdir(parents=True, exist_ok=True)
        memory_file = council_dir / "MEMORY.md"

        # Check existing file for operator manual edits
        operator_section = None
        has_operator_override = False
        meta = self._load_meta(council_id)

        if memory_file.exists() and meta and not force_full_rebuild:
            try:
                current_disk_content = memory_file.read_text(encoding="utf-8")
                current_disk_hash = hashlib.sha256(current_disk_content.encode("utf-8")).hexdigest()
                last_generated_hash = meta.get("content_hash")

                if last_generated_hash and current_disk_hash != last_generated_hash:
                    has_operator_override = True
                    # Extract any custom operator lines if marker exists, or retain whole manual addendum
                    if "## 4. Operator Overrides & Manual Annotations" in current_disk_content:
                        parts = current_disk_content.split("## 4. Operator Overrides & Manual Annotations")
                        if len(parts) > 1:
                            subparts = parts[1].split("---")
                            operator_section = subparts[0].strip()
                    else:
                        operator_section = "> *Preserved manual operator note:*\n" + "\n".join(
                            f"> {line}" for line in current_disk_content.splitlines()[-10:] if line.strip()
                        )
            except Exception as e:
                logger.warning(f"Error checking operator overrides on {memory_file}: {e}")

        # Render deterministic markdown
        rendered_md = self.render_markdown(
            council_id=council_id,
            council_info=council_info,
            decisions=decisions,
            roles=roles,
            relationships=relationships,
            operator_section=operator_section,
        )

        content_hash = hashlib.sha256(rendered_md.encode("utf-8")).hexdigest()

        # Atomic file write (temp file + replace)
        tmp_file = memory_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(rendered_md)
            f.flush()
            os.fsync(f.fileno())

        tmp_file.replace(memory_file)

        # Update metadata
        new_meta = {
            "council_id": council_id,
            "profile_id": self.profile_id,
            "last_seq": max_seq,
            "content_hash": content_hash,
            "updated_at": time.time(),
            "decisions_count": len(decisions),
            "members": council_info.get("members", []),
            "has_operator_override": has_operator_override,
        }
        self._save_meta(council_id, new_meta)

        return CouncilMemoryRecord(
            council_id=council_id,
            profile_id=self.profile_id,
            last_seq=max_seq,
            content_hash=content_hash,
            updated_at=new_meta["updated_at"],
            file_path=str(memory_file),
            decisions_count=len(decisions),
            members=council_info.get("members", []),
            has_operator_override=has_operator_override,
        )

    def rebuild_all_councils(self, force: bool = True) -> Dict[str, CouncilMemoryRecord]:
        """Discover and project MEMORY.md for all councils found in the EventStore."""
        councils = set()
        for ev in self.store.get_all():
            if ev.name in ("civ.council.created", "civ.council.registered"):
                spec_id = (ev.payload or {}).get("spec", {}).get("id")
                if spec_id:
                    councils.add(spec_id)
            elif ev.name in ("civ.council.decision-recorded", "agent_council.decision"):
                rec = (ev.payload or {}).get("decision") or (ev.payload or {}).get("decision_record") or (ev.payload or {})
                c_id = rec.get("council_id")
                if c_id:
                    councils.add(c_id)

        results = {}
        for c_id in sorted(councils):
            results[c_id] = self.project_council(c_id, force_full_rebuild=force)
        return results

    def project_all(self) -> Dict[str, str]:
        """Project all councils if updated, returning mapping of council_id -> file_path string."""
        records = self.rebuild_all_councils(force=False)
        return {cid: rec.file_path for cid, rec in records.items()}

    def rebuild_all(self) -> Dict[str, str]:
        """Force full rebuild of all councils, returning mapping of council_id -> file_path string."""
        records = self.rebuild_all_councils(force=True)
        return {cid: rec.file_path for cid, rec in records.items()}


__all__ = ["CouncilMemoryProjectionEngine", "CouncilMemoryRecord"]
