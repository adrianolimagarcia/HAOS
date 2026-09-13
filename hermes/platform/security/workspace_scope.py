"""HAOS WorkspaceScope: Formal isolation between Agent Workspace and Project Workspace.

Ported in spirit from HKUDS/nanobot's WorkspaceScope:
- Agent Workspace: ~/.hermes (config.yaml, .env, sessions, memory, OKF, ADR vault).
- Project Workspace: The codebase / directory the user asked the agent to inspect or edit.

Rules:
1. General code-editing tools (write_file, patch) operate inside the Project Workspace.
2. Direct writes into the Agent Workspace (~/.hermes) are blocked by default so that
   ordinary code editing cannot corrupt or tamper with agent configuration, credentials,
   memories, or session history.
3. Dedicated agent tools ('skill_manage', HAOS memory tools, 'hermes config') are the
   sanctioned entry points to mutate the Agent Workspace.
4. If the user intentionally runs the agent from inside the Agent Workspace (developing
   within ~/.hermes), the boundary relaxes so the user is not locked out.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class WorkspaceScope:
    """Resolved workspace boundaries for the current operation."""

    agent_workspace: Path
    project_workspace: Path
    is_isolated: bool

    def is_inside_agent_workspace(self, target_path: str | Path) -> bool:
        """Return True if *target_path* resolves to or inside the Agent Workspace."""
        try:
            resolved = Path(os.path.expanduser(str(target_path))).resolve()
            agent_real = self.agent_workspace.resolve()
            if resolved == agent_real:
                return True
            try:
                resolved.relative_to(agent_real)
                return True
            except ValueError:
                return False
        except (OSError, RuntimeError, ValueError):
            return False

    def is_write_permitted(self, target_path: str | Path) -> tuple[bool, Optional[str]]:
        """Check whether general file tools may write to *target_path*."""
        if not self.is_isolated:
            return True, None

        if self.is_inside_agent_workspace(target_path):
            return False, (
                f"Refusing to write to Agent Workspace: {target_path}\n"
                "General file tools are scoped to the Project Workspace and cannot modify "
                "~/.hermes state. Use dedicated tools ('skill_manage', HAOS memory tools, "
                "or 'hermes config') to manage agent configuration or state."
            )
        return True, None


def resolve_workspace_scope(
    project_dir: Optional[str | Path] = None,
    agent_dir: Optional[str | Path] = None,
) -> WorkspaceScope:
    """Resolve the active Agent Workspace and Project Workspace boundaries."""
    if agent_dir is not None:
        agent_ws = Path(os.path.expanduser(str(agent_dir))).resolve()
    else:
        try:
            from hermes_constants import get_hermes_home
            agent_ws = Path(get_hermes_home()).resolve()
        except Exception:
            agent_ws = Path(os.path.expanduser("~/.hermes")).resolve()

    if project_dir is not None:
        proj_ws = Path(os.path.expanduser(str(project_dir))).resolve()
    else:
        try:
            proj_ws = Path.cwd().resolve()
        except Exception:
            proj_ws = Path(".").resolve()

    # If the project workspace IS the agent workspace or sits inside it, isolation is disabled.
    try:
        is_isolated = not (proj_ws == agent_ws or agent_ws in proj_ws.parents)
    except Exception:
        is_isolated = True

    return WorkspaceScope(
        agent_workspace=agent_ws,
        project_workspace=proj_ws,
        is_isolated=is_isolated,
    )
