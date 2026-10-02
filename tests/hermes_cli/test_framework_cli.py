"""Real-import operational framework CLI contracts; never use live operator state."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_cli.main import _build_cli_parser
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


ROOT = Path(__file__).resolve().parents[2]


def test_framework_profile_cycle_isolation_and_safe_modes(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HAOS_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "launch"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    parser, _ = _build_cli_parser()
    homes = [tmp_path / "a", tmp_path / "b"]
    cycle_ids = {}
    for home in [homes[0], homes[1], homes[0]]:
        token = set_hermes_home_override(home)
        try:
            args = parser.parse_args(["framework", "status", "--json"])
            assert args.func(args) == 0
            status = json.loads(capsys.readouterr().out)
            assert status["base_dir"] == str(home / "agent")
            if home not in cycle_ids:
                assert status["initialized"] is False
                assert not (home / "agent").exists()
                args = parser.parse_args(["framework", "observe", "--json"])
                assert args.func(args) == 0
                observed = json.loads(capsys.readouterr().out)
                assert "metrics" in observed["telemetry"]
                wal_files = observed["telemetry"]["metrics"]["wal_files"]
                assert wal_files[0]["path"] == str(home / "state.db-wal")
                assert not (home / "agent").exists()
                args = parser.parse_args(["framework", "run", "--json"])
                assert args.func(args) == 0
                record = json.loads(capsys.readouterr().out)
                assert record["dry_run"] is True
                assert record["autonomous"] is False
                assert record["outcome"] == "dry_run"
                assert record["verification"]["verified"] is False
                cycle_ids[home] = record["cycle_id"]
            else:
                assert status["state"]["cycle_id"] == cycle_ids[home]
        finally:
            reset_hermes_home_override(token)
    assert cycle_ids[homes[0]] != cycle_ids[homes[1]]
    for unsafe in ["--apply", "--autonomous"]:
        with pytest.raises(SystemExit) as error:
            parser.parse_args(["framework", "run", unsafe])
        assert error.value.code == 2


def test_framework_saved_plan_approval_boundary(tmp_path, capsys):
    from hermes.platform.agent_framework.models import ExecutionPlan, PlanStep, RiskTier
    from hermes.platform.agent_framework.pipeline import HAOSOrchestrator

    base = tmp_path / "agent"
    orchestrator = HAOSOrchestrator(base_dir=base)
    step = PlanStep(step_id="report", order=1, action_name="workspace_config_update",
                    target=str(base / "artifacts" / "operational-report.json"),
                    params={"content": {"reviewed": True}}, risk_tier=RiskTier.MODERATE)
    plan = ExecutionPlan(plan_id="reviewed-plan", title="Reviewed report", steps=[step])
    orchestrator.run_cycle(plan=plan)
    parser, _ = _build_cli_parser()
    args = parser.parse_args(["framework", "approve", plan.plan_id, "--step", step.step_id,
                              "--base-dir", str(base), "--json"])
    assert args.func(args) == 0
    approval = json.loads(capsys.readouterr().out)
    assert approval["step_id"] == step.step_id
    assert approval["target"] == step.target
    args = parser.parse_args(["framework", "apply", plan.plan_id, "--approval", approval["approval_id"],
                              "--base-dir", str(base), "--json"])
    args.func(args)
    denied = json.loads(capsys.readouterr().out)
    assert denied.get("verification", {}).get("verified") is not True
    assert not Path(step.target).exists()  # Consent never substitutes for an exact policy grant.
    args = parser.parse_args(["framework", "approve", "../escape", "--step", step.step_id,
                              "--base-dir", str(base), "--json"])
    assert args.func(args) == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_framework_report_plan_grant_approve_apply_real_flow(tmp_path, capsys):
    parser, _ = _build_cli_parser()
    base = tmp_path / "agent"

    def invoke(parts):
        args = parser.parse_args(["framework", *parts, "--base-dir", str(base), "--json"])
        code = args.func(args)
        payload = json.loads(capsys.readouterr().out)
        assert code == 0, payload
        return payload

    preview = invoke(["plan", "--report"])
    plan = preview["plan"]
    assert preview["dry_run"] is True
    step = plan["steps"][0]
    target = base / "artifacts" / "operational-report.json"
    assert step["target"] == str(target)
    assert not target.exists()
    invoke(["grant", plan["plan_id"], "--step", step["step_id"]])
    approval = invoke(["approve", plan["plan_id"], "--step", step["step_id"]])
    applied = invoke(["apply", plan["plan_id"], "--approval", approval["approval_id"]])
    assert applied["verification"]["verified"] is True
    assert json.loads(target.read_text()) == step["params"]["content"]
    args = parser.parse_args(["framework", "apply", plan["plan_id"], "--approval", approval["approval_id"],
                              "--base-dir", str(base), "--json"])
    assert args.func(args) == 1
    assert "error" in json.loads(capsys.readouterr().out)

    autonomous_preview = invoke(["plan", "--report"])
    autonomous_plan = autonomous_preview["plan"]
    autonomous_step = autonomous_plan["steps"][0]
    invoke(["grant", autonomous_plan["plan_id"], "--step", autonomous_step["step_id"], "--autonomous"])
    autonomous_result = invoke(["apply", autonomous_plan["plan_id"], "--mode", "autonomous"])
    assert autonomous_result["autonomous"] is True
    assert autonomous_result["verification"]["verified"] is True


@pytest.mark.parametrize("entry", [["-m", "hermes_cli.main"], [str(ROOT / "bin" / "haos")]])
def test_framework_reachable_entrypoints(tmp_path, entry):
    env = os.environ.copy()
    env.pop("HAOS_HOME", None)
    env["HERMES_HOME"] = str(tmp_path / "profile")
    env["HOME"] = str(tmp_path)
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, *entry, "framework", "status", "--json"],
        env=env, cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["initialized"] is False
    assert status["base_dir"] == str(tmp_path / "profile" / "agent")
    assert not (tmp_path / "profile" / "agent").exists()
