"""Tests for CrewAI-inspired Typed Output Contracts & Upstream Input Handoff."""

import json
import tempfile
from pathlib import Path
import pytest

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.execution.contracts import TaskIOContract, FieldContract
from hermes.platform.execution.lane_executor import DeterministicLaneWorker, HermesCliLaneWorker, LaneError


def test_task_spec_with_crewai_contract():
    contract = TaskIOContract(
        inputs=[FieldContract(name="query", type="str", required=True)],
        outputs=[
            FieldContract(name="summary", type="str", required=True),
            FieldContract(name="score", type="int", required=True),
        ],
    )
    spec = TaskSpec(
        id="T-CREW-1",
        title="Analyze code quality",
        goal="Run analysis and return structured metrics",
        expected_output="Detailed code review with integer score (0-100)",
        upstream_inputs={"upstream_task_id": "T-PREV", "ast_nodes": 42},
        task_contract=contract,
    )
    assert spec.expected_output == "Detailed code review with integer score (0-100)"
    assert spec.upstream_inputs["ast_nodes"] == 42
    assert spec.task_contract.outputs[1].name == "score"

    # Test roundtrip from_dict
    d = spec.__dict__
    reconstructed = TaskSpec.from_dict(d)
    assert reconstructed.expected_output == spec.expected_output
    assert reconstructed.upstream_inputs == spec.upstream_inputs


def test_deterministic_worker_materializes_inputs_and_expected_output():
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        worker = DeterministicLaneWorker()
        spec_dict = {
            "id": "T-101",
            "goal": "Test handoff",
            "expected_output": "Markdown executive summary",
            "upstream_inputs": {"prior_decision": "use_sqlite"},
        }
        res = worker.execute(task_id="T-101", workspace=workspace, spec=spec_dict)
        assert res["status"] == "COMPLETED"

        # Check materialized files
        haos_dir = workspace / ".haos"
        assert (haos_dir / "inputs.json").exists()
        assert json.loads((haos_dir / "inputs.json").read_text(encoding="utf-8")) == {"prior_decision": "use_sqlite"}
        assert (haos_dir / "expected_output.txt").exists()
        assert (haos_dir / "expected_output.txt").read_text(encoding="utf-8") == "Markdown executive summary"


def test_contract_validation_in_read_result():
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        haos_dir = workspace / ".haos"
        haos_dir.mkdir(parents=True, exist_ok=True)
        result_file = haos_dir / "result.json"

        contract = TaskIOContract(
            outputs=[
                FieldContract(name="summary", type="str", required=True),
                FieldContract(name="metrics", type="json", required=True),
            ]
        )
        spec = {
            "id": "T-VAL",
            "task_contract": contract.to_dict(),
        }

        # Case 1: Valid payload conforming to contract
        valid_payload = {
            "summary": "Everything succeeded",
            "metrics": {"loc": 1500, "tests_passed": 42},
            "completed": ["All checks green"],
        }
        result_file.write_text(json.dumps(valid_payload), encoding="utf-8")

        res = HermesCliLaneWorker._read_result(
            task_id="T-VAL",
            result_file=result_file,
            pid=1234,
            returncode=0,
            workspace=workspace,
            spec=spec,
        )
        assert res["status"] == "COMPLETED"
        assert res["summary"] == "Everything succeeded"

        # Case 2: Missing required field 'metrics'
        invalid_payload = {
            "summary": "Missing metrics",
            "completed": [],
        }
        result_file.write_text(json.dumps(invalid_payload), encoding="utf-8")

        with pytest.raises(LaneError) as exc_info:
            HermesCliLaneWorker._read_result(
                task_id="T-VAL",
                result_file=result_file,
                pid=1234,
                returncode=0,
                workspace=workspace,
                spec=spec,
            )
        assert "failed expected output contract" in str(exc_info.value)
        assert "missing_required" in str(exc_info.value)
