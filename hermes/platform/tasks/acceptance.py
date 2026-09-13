import shlex
import subprocess
from typing import Dict, Any, List, Optional
from hermes.platform.tasks.spec import AcceptanceCriterion


class AcceptanceEngine:
    @staticmethod
    def validate_criterion(criterion: AcceptanceCriterion, context: Dict[str, Any]) -> Dict[str, Any]:
        if criterion.type == "structural":
            # Fail-closed: se structural_ok não foi fornecido explicitamente, exige evidência
            if "structural_ok" not in context and "expected_files" not in context:
                return {"id": criterion.id, "passed": False, "reason": "No structural evidence provided in context"}
            passed = context.get("structural_ok", True)
            return {"id": criterion.id, "passed": passed, "reason": "Structural validation evaluated"}
        
        elif criterion.type == "test":
            if criterion.command:
                try:
                    cwd = context.get("workspace_path") or context.get("cwd") or None
                    cmd_parts = shlex.split(criterion.command)
                    res = subprocess.run(cmd_parts, cwd=cwd, capture_output=True, text=True, timeout=30)
                    passed = (res.returncode == 0)
                    return {"id": criterion.id, "passed": passed, "stdout": res.stdout, "stderr": res.stderr}
                except Exception as e:
                    return {"id": criterion.id, "passed": False, "error": str(e)}
            return {"id": criterion.id, "passed": False, "reason": "No test command provided"}

        elif criterion.type == "lsp":
            if "lsp_diagnostics" not in context:
                return {"id": criterion.id, "passed": False, "reason": "No LSP diagnostics provided in context"}
            diagnostics = context.get("lsp_diagnostics", {})
            new_errors = diagnostics.get("new_errors", 0)
            return {"id": criterion.id, "passed": (new_errors == 0), "new_errors": new_errors}

        elif criterion.type == "schema":
            from hermes.platform.execution.contracts import validate_contract
            output_data = context.get("output_data")
            field_contracts = context.get("field_contracts")
            if output_data is None or field_contracts is None:
                return {"id": criterion.id, "passed": False, "reason": "Missing output_data or field_contracts for schema validation"}
            violations = validate_contract(output_data, field_contracts)
            return {"id": criterion.id, "passed": (len(violations) == 0), "violations": violations}

        elif criterion.type == "review_score":
            if "reviewer_score" not in context and "review_verdicts" not in context:
                return {"id": criterion.id, "passed": False, "reason": "No reviewer score or verdict provided in context"}
            score = context.get("reviewer_score", 0.0)
            approved = context.get("review_approved", True)
            passed = (score >= 0.8) and approved
            return {"id": criterion.id, "passed": passed, "score": score, "review_approved": approved}

        return {"id": criterion.id, "passed": False, "reason": f"Unknown criterion type '{criterion.type}' (fail-closed)"}


class CompositeAcceptanceEngine(AcceptanceEngine):
    """
    Estende AcceptanceEngine para suportar composição lógica de critérios:
    - all_of: Todos os critérios no bloco devem ser satisfeitos.
    - any_of: Pelo menos um critério no bloco deve ser satisfeito.
    Combina validadores mecânicos (testes subprocess, structural, LSP)
    e score de review (review_score / review verdicts).
    """

    @classmethod
    def evaluate_composite(
        cls,
        spec_or_criteria: Any,
        context: Dict[str, Any],
        composition_mode: str = "all_of",
        review_verdicts: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """
        Avalia um conjunto de critérios (ou spec.acceptance_criteria) sob 'all_of' ou 'any_of'.
        Se review_verdicts forem fornecidos, integra os scores de revisão no contexto.
        """
        criteria: List[AcceptanceCriterion] = []
        if hasattr(spec_or_criteria, "acceptance_criteria"):
            criteria = list(spec_or_criteria.acceptance_criteria)
        elif isinstance(spec_or_criteria, list):
            criteria = list(spec_or_criteria)
        elif isinstance(spec_or_criteria, AcceptanceCriterion):
            criteria = [spec_or_criteria]

        eval_context = dict(context)

        # Se houver review_verdicts passados, calcula o score médio ou ponderado de review
        if review_verdicts:
            scores = [v.score for v in review_verdicts if hasattr(v, "score")]
            all_approved = all(getattr(v, "approved", True) for v in review_verdicts)
            if scores:
                eval_context["reviewer_score"] = sum(scores) / len(scores)
            eval_context["review_approved"] = all_approved
            eval_context["review_verdicts"] = review_verdicts

        results = []
        for crit in criteria:
            res = cls.validate_criterion(crit, eval_context)
            results.append(res)

        if not results:
            return {
                "passed": True,
                "mode": composition_mode,
                "results": [],
                "summary": "No criteria to evaluate",
            }

        if composition_mode == "any_of":
            passed = any(r.get("passed", False) for r in results)
        else: # all_of por padrão
            passed = all(r.get("passed", False) for r in results)

        failed_reasons = [
            f"{r.get('id')}: {r.get('error') or r.get('reason') or 'failed'}"
            for r in results
            if not r.get("passed", False)
        ]

        return {
            "passed": passed,
            "mode": composition_mode,
            "results": results,
            "failed_count": sum(1 for r in results if not r.get("passed", False)),
            "passed_count": sum(1 for r in results if r.get("passed", False)),
            "summary": "All criteria met" if passed else f"Failures: {'; '.join(failed_reasons)}",
        }
