"""Contract tests for Shanraisshan Agentic Engineering Patterns in HAOS.

Covers:
1. Modular Path Rules (.haos/rules/*.md & .claude/rules/*.md) with lazy path-matching.
2. Vertical PRD Slices in DAGWorkflowBuilder.
3. Trigger-First Skill Description Curation standards.
"""

from pathlib import Path
from agent.subdirectory_hints import SubdirectoryHintTracker
from hermes.platform.workflow.durable import DAGWorkflowBuilder
from agent.curator import CURATOR_REVIEW_PROMPT


def test_modular_path_rules_lazy_loading(tmp_path: Path):
    # Setup working dir with .haos/rules
    rules_dir = tmp_path / ".haos" / "rules"
    rules_dir.mkdir(parents=True)

    cli_rule = rules_dir / "cli_guidelines.md"
    cli_rule.write_text(
        "---\n"
        "paths:\n"
        "  - 'hermes_cli/**/*.py'\n"
        "---\n"
        "# CLI Rule\n"
        "Always test CLI commands with JSON flag.\n",
        encoding="utf-8",
    )

    db_rule = rules_dir / "db_guidelines.md"
    db_rule.write_text(
        "---\n"
        "paths:\n"
        "  - 'db/**/*.sql'\n"
        "---\n"
        "# DB Rule\n"
        "Always use WAL mode.\n",
        encoding="utf-8",
    )

    tracker = SubdirectoryHintTracker(working_dir=str(tmp_path))

    # Touching a non-matching file does not load CLI or DB rules
    res1 = tracker.check_tool_call("read_file", {"file_path": str(tmp_path / "README.md")})
    assert res1 is None

    # Touching a matching file in hermes_cli triggers cli_guidelines.md
    res2 = tracker.check_tool_call("read_file", {"file_path": str(tmp_path / "hermes_cli" / "main.py")})
    assert res2 is not None
    assert "Always test CLI commands with JSON flag." in res2
    assert "db_guidelines.md" not in res2

    # Second touch of hermes_cli does not re-inject the rule (idempotent context)
    res3 = tracker.check_tool_call("read_file", {"file_path": str(tmp_path / "hermes_cli" / "utils.py")})
    assert res3 is None


def test_vertical_prd_slice_in_dag_workflow():
    builder = DAGWorkflowBuilder("auth_epic")
    builder.add_vertical_slice(
        slice_id="slice-login",
        feature_name="OAuth2 Login Flow",
        acceptance_criteria=[
            "Endpoint POST /auth/login returns JWT",
            "Expired tokens return HTTP 401",
        ],
        test_command="pytest tests/test_auth.py",
    )

    assert "slice-login" in builder.tasks
    task = builder.tasks["slice-login"]
    assert task.title == "Slice: OAuth2 Login Flow"
    assert "Vertical PRD Slice: OAuth2 Login Flow" in task.body
    assert "Endpoint POST /auth/login returns JWT" in task.body
    assert "pytest tests/test_auth.py" in task.body
    assert builder.validate_acyclic() is True


def test_curator_prompt_enforces_trigger_first():
    assert "TRIGGER-FIRST FORMAT" in CURATOR_REVIEW_PROMPT
    assert "Use when..." in CURATOR_REVIEW_PROMPT
