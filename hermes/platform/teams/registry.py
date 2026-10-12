"""Registry and Execution Orchestrator for Declarative Team Templates."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import yaml

from hermes_constants import get_hermes_home
from hermes.platform.teams.schema import TeamTemplate, TeamRole, CoordinationMode


_BUILTINS_DIR = Path(__file__).parent / "builtins"


def get_user_teams_dir() -> Path:
    """Return the user directory for custom team templates (~/.hermes/teams)."""
    return Path(get_hermes_home()) / "teams"


def load_builtin_templates() -> Dict[str, TeamTemplate]:
    """Load all built-in team templates packaged with HAOS."""
    templates: Dict[str, TeamTemplate] = {}
    if not _BUILTINS_DIR.exists():
        return templates

    for yaml_file in _BUILTINS_DIR.glob("*.yaml"):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
            if data and isinstance(data, dict):
                tmpl = TeamTemplate.model_validate(data)
                templates[tmpl.name] = tmpl
        except Exception:
            pass
    return templates


def load_user_templates(teams_dir: Optional[Path] = None) -> Dict[str, TeamTemplate]:
    """Load custom user team templates."""
    templates: Dict[str, TeamTemplate] = {}
    target_dir = teams_dir or get_user_teams_dir()
    if not target_dir.exists():
        return templates

    for yaml_file in target_dir.glob("*.yaml"):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
            if data and isinstance(data, dict):
                tmpl = TeamTemplate.model_validate(data)
                templates[tmpl.name] = tmpl
        except Exception:
            pass
    return templates


def list_team_templates(teams_dir: Optional[Path] = None) -> List[TeamTemplate]:
    """Return all available team templates (built-in + user)."""
    builtins = load_builtin_templates()
    users = load_user_templates(teams_dir)
    merged = {**builtins, **users}
    return sorted(merged.values(), key=lambda t: t.name)


def get_team_template(name: str, teams_dir: Optional[Path] = None) -> TeamTemplate:
    """Fetch a team template by name, raising FileNotFoundError if missing."""
    all_templates = {t.name: t for t in list_team_templates(teams_dir)}
    if name not in all_templates:
        raise FileNotFoundError(f"Team template '{name}' not found")
    return all_templates[name]


def execute_sequential_team(
    team: TeamTemplate,
    goal: str,
    executor: Callable[[TeamRole, str, Dict[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    """Execute a team sequentially, passing outputs across roles.

    executor signature: (role: TeamRole, goal: str, context: dict) -> dict
    """
    if team.coordination.mode != CoordinationMode.sequential:
        raise NotImplementedError(f"Coordination mode {team.coordination.mode} is not yet implemented")

    shared_context: Dict[str, Any] = {"goal": goal}
    step_results: List[Dict[str, Any]] = []

    for role in team.roles:
        res = executor(role, goal, shared_context)
        step_results.append({
            "role": role.name,
            "result": res,
        })
        # Merge outputs into shared context
        if isinstance(res, dict):
            for k, v in res.items():
                if not team.coordination.shared_context or k in team.coordination.shared_context:
                    shared_context[k] = v

    return {
        "team": team.name,
        "goal": goal,
        "steps": step_results,
        "final_context": shared_context,
    }


_SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"glpat-[a-zA-Z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[^\s'\"]+['\"]?", re.IGNORECASE),
]
_SECRET_KEY_NAMES = {"secret", "api_key", "token", "password", "auth_token", "private_key", "credentials"}


def _sanitize_data(data: Any) -> Any:
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            if any(s in k.lower() for s in _SECRET_KEY_NAMES):
                continue
            cleaned[k] = _sanitize_data(v)
        return cleaned
    elif isinstance(data, list):
        return [_sanitize_data(x) for x in data]
    elif isinstance(data, str):
        val = data
        for pat in _SECRET_PATTERNS:
            val = pat.sub("[REDACTED_SECRET]", val)
        return val
    return data


def export_team_template_markdown(template: TeamTemplate, playbook_markdown: str = "") -> str:
    """Export a team template as Markdown with sanitized YAML frontmatter and playbook body."""
    raw_dict = template.model_dump(exclude_none=True)
    sanitized = _sanitize_data(raw_dict)
    yaml_header = yaml.safe_dump(sanitized, sort_keys=False, allow_unicode=True).strip()
    body = (playbook_markdown or f"# {template.name} Playbook\n\n{template.description}").strip()
    body = _sanitize_data(body)
    full_content = f"---\n{yaml_header}\n---\n\n{body}\n"
    return _sanitize_data(full_content)


def import_team_template_markdown(markdown_text: str, inert: bool = True) -> Tuple[TeamTemplate, str]:
    """Import a team template from Markdown with YAML frontmatter.

    If inert is True (default OpenMausBot security standard):
    - skills_enabled is set to False for all roles.
    - any routines are paused.
    """
    pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
    match = pattern.match(markdown_text.strip())
    if not match:
        raise ValueError("Invalid format: Markdown file must contain YAML frontmatter delimited by '---'")

    yaml_str, body = match.groups()
    try:
        data = yaml.safe_load(yaml_str)
    except Exception as e:
        raise ValueError(f"YAML frontmatter parsing failed: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("YAML frontmatter must be a valid mapping dictionary")

    if inert:
        for role in data.get("roles", []):
            if isinstance(role, dict):
                role["skills_enabled"] = False
        for routine in data.get("routines", []):
            if isinstance(routine, dict):
                routine["paused"] = True
                routine["active"] = False

    template = TeamTemplate.model_validate(data)
    return template, body.strip()
