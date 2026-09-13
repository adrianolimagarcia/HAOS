"""Contract tests for HAOS WorkspaceScope (Agent vs Project Workspace isolation)."""

from pathlib import Path
import pytest

from hermes.platform.security.workspace_scope import (
    WorkspaceScope,
    resolve_workspace_scope,
)
from tools.file_tools import write_file_tool


def test_workspace_scope_resolution(tmp_path: Path):
    agent_dir = tmp_path / "hermes_home"
    agent_dir.mkdir()
    project_dir = tmp_path / "project_repo"
    project_dir.mkdir()

    scope = resolve_workspace_scope(project_dir=project_dir, agent_dir=agent_dir)
    assert scope.is_isolated is True
    assert scope.agent_workspace == agent_dir.resolve()
    assert scope.project_workspace == project_dir.resolve()


def test_workspace_scope_same_dir_not_isolated(tmp_path: Path):
    agent_dir = tmp_path / "hermes_home"
    agent_dir.mkdir()

    scope = resolve_workspace_scope(project_dir=agent_dir, agent_dir=agent_dir)
    assert scope.is_isolated is False


def test_workspace_scope_detects_inside_agent_workspace(tmp_path: Path):
    agent_dir = tmp_path / "hermes_home"
    agent_dir.mkdir()
    project_dir = tmp_path / "project_repo"
    project_dir.mkdir()

    scope = resolve_workspace_scope(project_dir=project_dir, agent_dir=agent_dir)
    assert scope.is_inside_agent_workspace(agent_dir / "config.yaml") is True
    assert scope.is_inside_agent_workspace(agent_dir / "memories" / "MEMORY.md") is True
    assert scope.is_inside_agent_workspace(project_dir / "src" / "main.py") is False


def test_write_file_tool_refuses_agent_workspace_write(tmp_path: Path, monkeypatch):
    agent_dir = tmp_path / "agent_home"
    agent_dir.mkdir()
    project_dir = tmp_path / "project_repo"
    project_dir.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(agent_dir))
    monkeypatch.chdir(project_dir)

    target_agent_file = agent_dir / "injected.py"
    res = write_file_tool(str(target_agent_file), "malicious code")
    assert "Refusing to write to Agent Workspace" in res
    assert not target_agent_file.exists()

    # Allowed in project workspace
    target_project_file = project_dir / "allowed.py"
    res_allowed = write_file_tool(str(target_project_file), "x = 1\n")
    assert "bytes_written" in res_allowed
    assert target_project_file.exists()
    assert target_project_file.read_text() == "x = 1\n"
