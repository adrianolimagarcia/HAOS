"""Contract tests for HAOS operational skills and safety invariants.

Validates:
1. Frontmatter metadata structure (name, description, tags/triggers, version, license, platforms).
2. Presence and order of required operational governance sections:
   - Diagnóstico Read-Only
   - Pré-condições
   - Snapshot
   - Autorização
   - Rollback
3. Simulation of read-only diagnostic checks without side effects or mutations.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

import pytest

OPERATIONAL_SKILLS_DIR = (
    Path(__file__).resolve().parents[2] / "skills" / "operations"
)

EXPECTED_SKILLS = [
    "haos-gateway-ops",
    "haos-mcp-ops",
    "haos-tws-hwa-ops",
    "haos-kanban-rust-ops",
    "haos-memory-ops",
]

REQUIRED_GOVERNANCE_SECTIONS = [
    "Diagnóstico Read-Only",
    "Pré-condições",
    "Snapshot",
    "Autorização",
    "Rollback",
]

REQUIRED_FRONTMATTER_FIELDS = [
    "name",
    "description",
    "version",
    "author",
    "license",
    "platforms",
    "category",
    "tags",
]


def _parse_frontmatter(path: Path) -> tuple[Dict[str, Any], str]:
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\n"), f"{path} must start with YAML frontmatter delimiter"
    parts = content.split("---\n", 2)
    assert len(parts) >= 3, f"{path} missing closing frontmatter delimiter"

    frontmatter_raw = parts[1]
    body = parts[2]

    metadata: Dict[str, Any] = {}
    for line in frontmatter_raw.strip().splitlines():
        if ":" in line and not line.strip().startswith("-"):
            key, val = line.split(":", 1)
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                items = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
                metadata[key.strip()] = items
            else:
                metadata[key.strip()] = val.strip("'\"")

    return metadata, body


@pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
def test_operational_skill_exists(skill_name: str) -> None:
    skill_file = OPERATIONAL_SKILLS_DIR / skill_name / "SKILL.md"
    assert skill_file.is_file(), f"Skill file does not exist: {skill_file}"


@pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
def test_operational_skill_frontmatter_contract(skill_name: str) -> None:
    skill_file = OPERATIONAL_SKILLS_DIR / skill_name / "SKILL.md"
    metadata, _ = _parse_frontmatter(skill_file)

    for field in REQUIRED_FRONTMATTER_FIELDS:
        assert field in metadata, f"{skill_name} missing frontmatter field '{field}'"
        assert metadata[field], f"{skill_name} field '{field}' cannot be empty"

    assert metadata["name"] == skill_name
    assert metadata["category"] == "operations"

    # Description constraints (hardline standards)
    description = metadata["description"]
    assert len(description) <= 60, f"{skill_name} description exceeds 60 chars ({len(description)})"
    assert description.endswith("."), f"{skill_name} description must end with a period"

    # Tags / Triggers check
    tags = metadata.get("tags", [])
    assert isinstance(tags, list) and len(tags) >= 3, f"{skill_name} must have at least 3 trigger tags"
    assert "operations" in tags, f"{skill_name} tags must include 'operations'"
    assert "haos" in tags, f"{skill_name} tags must include 'haos'"


@pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
def test_operational_skill_contract_sections(skill_name: str) -> None:
    skill_file = OPERATIONAL_SKILLS_DIR / skill_name / "SKILL.md"
    _, body = _parse_frontmatter(skill_file)

    # Standard sections
    standard_sections = [
        "## When to Use",
        "## Prerequisites",
        "## How to Run",
        "## Quick Reference",
        "## Procedure",
        "## Pitfalls",
        "## Verification",
    ]
    for section in standard_sections:
        assert section in body, f"{skill_name} missing standard section '{section}'"

    # Governed operational subsections inside Procedure or body
    for section in REQUIRED_GOVERNANCE_SECTIONS:
        # Match headings like "1. **Diagnóstico Read-Only**:" or "### Diagnóstico Read-Only"
        pattern = rf"(\*\*|###\s*){re.escape(section)}"
        match = re.search(pattern, body, re.IGNORECASE)
        assert match is not None, (
            f"{skill_name} missing required governance subsection: '{section}'"
        )


@pytest.mark.parametrize("skill_name", EXPECTED_SKILLS)
def test_safety_invariants_and_destructive_action_prevention(skill_name: str) -> None:
    """Verifies safety policy: read-only by default, explicit operator authorization,

    and strict prevention of uncontrolled mutations.
    """
    skill_file = OPERATIONAL_SKILLS_DIR / skill_name / "SKILL.md"
    _, body = _parse_frontmatter(skill_file)

    # Must explicitly mention read-only posture
    assert "read-only" in body.lower(), f"{skill_name} must specify read-only diagnostic posture"

    # Must mandate operator authorization for corrective / mutative actions
    auth_matches = re.search(r"autorização", body, re.IGNORECASE)
    assert auth_matches is not None, f"{skill_name} must mandate operator authorization"

    # Must specify snapshot / backup before correction
    snapshot_matches = re.search(r"snapshot|backup", body, re.IGNORECASE)
    assert snapshot_matches is not None, f"{skill_name} must mandate snapshot/backup before changes"

    # Must define rollback plan
    rollback_matches = re.search(r"rollback", body, re.IGNORECASE)
    assert rollback_matches is not None, f"{skill_name} must provide a rollback strategy"


def test_tws_skill_prevents_manual_makeplan() -> None:
    """Specific check: haos-tws-hwa-ops must explicitly guard against manual MakePlan."""
    skill_file = OPERATIONAL_SKILLS_DIR / "haos-tws-hwa-ops" / "SKILL.md"
    _, body = _parse_frontmatter(skill_file)

    assert "MakePlan" in body, "haos-tws-hwa-ops must document MakePlan safeguards"
    assert re.search(r"prevenção|jamais execute `MakePlan`", body, re.IGNORECASE), (
        "haos-tws-hwa-ops must strictly warn against uncoordinated MakePlan execution"
    )


def test_simulated_read_only_diagnostics_have_no_side_effects(tmp_path: Path) -> None:
    """Simulate execution of diagnostics across components ensuring zero mutation."""
    # 1. Gateway diagnostic simulation
    fake_gateway_log = tmp_path / "gateway.log"
    fake_gateway_log.write_text("INFO: channel telegram connected\nINFO: channel slack connected\n")
    initial_log_hash = fake_gateway_log.stat().st_mtime
    # Read-only check
    lines = [l for l in fake_gateway_log.read_text().splitlines() if "connected" in l]
    assert len(lines) == 2
    assert fake_gateway_log.stat().st_mtime == initial_log_hash, "Gateway check caused side-effect"

    # 2. MCP schema & tools diagnostic simulation
    mcp_manifest = {
        "server": "ouroboros",
        "tools": [
            {
                "name": "ouroboros_status",
                "description": "Check status",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    }
    # Validate schema read-only
    for tool in mcp_manifest["tools"]:
        assert "name" in tool and "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"

    # 3. SQLite Kanban integrity simulation
    import sqlite3
    db_path = tmp_path / "kanban.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT);")
    cur.execute("INSERT INTO tasks VALUES ('task-1', 'READY');")
    conn.commit()

    # Diagnostic integrity query
    cur.execute("PRAGMA integrity_check;")
    result = cur.fetchone()[0]
    assert result == "ok"

    # Group count check
    cur.execute("SELECT status, count(*) FROM tasks GROUP BY status;")
    counts = dict(cur.fetchall())
    assert counts == {"READY": 1}
    conn.close()

    # 4. Memory Journal header verification simulation
    journal = tmp_path / "REGISTRY.md"
    journal.write_text("## Log recente\n- 2026-10-08 04:00 -03: Diagnóstico concluído.\n")
    initial_mtime = journal.stat().st_mtime
    corrupted_pattern = re.compile(r"-03 ·.*- 2026-")
    assert corrupted_pattern.search(journal.read_text()) is None
    assert journal.stat().st_mtime == initial_mtime, "Memory check mutated journal file"
