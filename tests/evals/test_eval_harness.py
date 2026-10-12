"""Testes automatizados do harness de evals do HAOS (Fase 7).

Verifica a validação de schema e a execução determinística de cenários contra a stack real.
"""

from pathlib import Path
import pytest
from pydantic import ValidationError

from evals.harness.runner import EvalRunner
from evals.harness.schema import EvalScenario


SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "evals" / "scenarios"


class TestEvalHarness:
    """Suíte de testes para o harness de evals puro em dados."""

    def test_scenarios_exist(self):
        assert SCENARIOS_DIR.exists()
        json_files = list(SCENARIOS_DIR.glob("*.json"))
        assert len(json_files) >= 3, f"Esperava ao menos 3 cenários, encontrou {len(json_files)}"

    @pytest.mark.parametrize("scenario_file", list(SCENARIOS_DIR.glob("*.json")), ids=lambda p: p.name)
    def test_scenario_schema_validation(self, scenario_file: Path):
        """Garante que todos os cenários são JSONs puros que validam estritamente contra o schema."""
        raw_text = scenario_file.read_text(encoding="utf-8")
        scenario = EvalScenario.model_validate_json(raw_text)
        assert scenario.name
        assert scenario.description
        assert len(scenario.turns) > 0
        assert len(scenario.assertions) > 0

    @pytest.mark.parametrize("scenario_file", list(SCENARIOS_DIR.glob("*.json")), ids=lambda p: p.name)
    def test_scenario_execution(self, scenario_file: Path):
        """Executa cada cenário contra a stack real e verifica que 100% das asserções passam."""
        runner = EvalRunner()
        result = runner.run_scenario_file(scenario_file)

        # Se falhou, exibe detalhes das asserções que falharam
        failed_details = [
            f"{r.target}: esperado '{r.expected}', obtido '{r.actual}' ({r.description})"
            for r in result.assertion_results
            if not r.passed
        ]
        assert result.passed, f"Cenário '{result.scenario_name}' falhou:\n" + "\n".join(failed_details)
        assert result.passed_assertions == result.total_assertions

    def test_invalid_scenario_schema_rejected(self):
        """Garante fail-closed se um cenário não cumprir o schema."""
        invalid_data = {
            "name": "invalid_scenario",
            # missing description
            "turns": [],
        }
        with pytest.raises(ValidationError):
            EvalScenario.model_validate(invalid_data)
