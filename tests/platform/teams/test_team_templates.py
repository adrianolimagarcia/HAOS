"""Tests for Declarative Team Templates Schema and Orchestration."""

import pytest
from pydantic import ValidationError

from hermes.platform.teams.schema import (
    TeamTemplate,
    TeamRole,
    CoordinationMode,
)
from hermes.platform.teams.registry import (
    get_team_template,
    list_team_templates,
    execute_sequential_team,
    export_team_template_markdown,
    import_team_template_markdown,
)


class TestTeamTemplates:
    def test_invalid_template_fails_validation(self):
        # Empty roles list
        with pytest.raises(ValidationError):
            TeamTemplate(
                name="invalid-empty",
                description="desc",
                roles=[],
            )

        # Empty role name
        with pytest.raises(ValidationError):
            TeamRole(
                name="",
                system_prompt="prompt",
            )

        # Duplicate role names
        with pytest.raises(ValidationError):
            TeamTemplate(
                name="duplicate-roles",
                description="desc",
                roles=[
                    TeamRole(name="agent1", system_prompt="p1"),
                    TeamRole(name="agent1", system_prompt="p2"),
                ],
            )

    def test_load_builtin_code_review_pair(self):
        team = get_team_template("code-review-pair")
        assert team.name == "code-review-pair"
        assert len(team.roles) == 2
        role_names = [r.name for r in team.roles]
        assert "lead" in role_names
        assert "reviewer" in role_names

        # Check reviewer has tools_denied
        reviewer = next(r for r in team.roles if r.name == "reviewer")
        assert "terminal" in reviewer.tools_denied
        assert "write_file" in reviewer.tools_denied
        assert "patch" in reviewer.tools_denied

    def test_role_tools_permission_enforcement(self):
        role = TeamRole(
            name="security_auditor",
            system_prompt="Audit code",
            tools_allowed=["read_file", "search_files"],
            tools_denied=["terminal", "write_file"],
        )

        assert role.is_tool_allowed("read_file") is True
        assert role.is_tool_allowed("search_files") is True
        assert role.is_tool_allowed("terminal") is False
        assert role.is_tool_allowed("write_file") is False
        assert role.is_tool_allowed("patch") is False  # not in tools_allowed

    def test_sequential_coordination_chains_context(self):
        from hermes.platform.teams.schema import TeamCoordination
        team = TeamTemplate(
            name="test-pipeline",
            description="2-step pipeline",
            roles=[
                TeamRole(name="step1", system_prompt="First step"),
                TeamRole(name="step2", system_prompt="Second step"),
            ],
            coordination=TeamCoordination(mode=CoordinationMode.sequential, shared_context=["step1_output", "step2_output"]),
        )

        def mock_executor(role: TeamRole, goal: str, context: dict):
            if role.name == "step1":
                return {"step1_output": f"processed_{goal}"}
            elif role.name == "step2":
                # Must be able to see step1's output
                prev = context.get("step1_output")
                return {"step2_output": f"final_{prev}"}
            return {}

        result = execute_sequential_team(team, "sample-task", mock_executor)
        assert result["team"] == "test-pipeline"
        assert len(result["steps"]) == 2
        assert result["final_context"]["step1_output"] == "processed_sample-task"
        assert result["final_context"]["step2_output"] == "final_processed_sample-task"

    def test_export_import_markdown_roundtrip(self):
        team = TeamTemplate(
            name="devops-trio",
            description="Trio for continuous deployment and monitoring",
            roles=[
                TeamRole(name="builder", system_prompt="Build container images", skills=["docker"]),
                TeamRole(name="tester", system_prompt="Run regression suites", skills=["pytest"]),
            ],
            routines=[{"name": "nightly_build", "schedule": "0 0 * * *"}],
            connection_slots=["slack_alerts", "github_webhook"],
        )
        md_text = export_team_template_markdown(team, "# DevOps Playbook\n\nRun instructions.")
        assert "name: devops-trio" in md_text
        assert "# DevOps Playbook" in md_text

        # Import without inert mode
        imported, body = import_team_template_markdown(md_text, inert=False)
        assert imported.name == team.name
        assert imported.description == team.description
        assert len(imported.roles) == 2
        assert imported.roles[0].skills == ["docker"]
        assert imported.roles[0].skills_enabled is True
        assert body == "# DevOps Playbook\n\nRun instructions."

    def test_export_sanitizes_secrets(self):
        secret_api_key = "sk-1234567890abcdef1234567890abcdef"
        bearer_token = "Bearer secret-bearer-token-12345"
        team = TeamTemplate(
            name="leak-test-team",
            description=f"Leaky prompt with {secret_api_key} inside description",
            roles=[
                TeamRole(
                    name="agent1",
                    system_prompt=f"Use authorization {bearer_token}",
                )
            ],
        )
        exported = export_team_template_markdown(team)
        assert secret_api_key not in exported
        assert bearer_token not in exported
        assert "[REDACTED_SECRET]" in exported

    def test_import_inert_by_default(self):
        raw_markdown = """---
name: external-team
description: Third-party untrusted team
roles:
  - name: worker
    system_prompt: Execute jobs
    skills:
      - dangerous_tool
    skills_enabled: true
routines:
  - name: poll_jobs
    active: true
    paused: false
---
# Untrusted Playbook
"""
        imported, _ = import_team_template_markdown(raw_markdown, inert=True)
        assert imported.roles[0].skills_enabled is False
        assert imported.routines[0]["paused"] is True
        assert imported.routines[0]["active"] is False

    def test_import_invalid_markdown_fails(self):
        with pytest.raises(ValueError, match="Invalid format"):
            import_team_template_markdown("Not a valid frontmatter markdown")

        with pytest.raises(ValueError):
            import_team_template_markdown("---\n: invalid yaml [[\n---\nbody")
