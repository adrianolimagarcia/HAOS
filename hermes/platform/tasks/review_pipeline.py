from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from hermes.platform.tasks.spec import TaskSpec, ReviewStage


@dataclass
class ReviewVerdict:
    """Veredito formal gerado por uma etapa de revisão."""
    stage_id: str
    posture: str
    approved: bool
    rationale: str
    score: float


class AntiAnchoringContextBuilder:
    """
    Construtor de contexto para revisores com isolamento estrito contra 'anchoring bias'.
    Garante que NÃO há transcripts, chain-of-thought interno, logs intermediários ou
    raciocínios do worker original. Apenas objetivo, critérios, artefatos,
    evidências mecânicas (testes/LSP) e riscos residuais declarados.
    """

    FORBIDDEN_KEYS = {
        "transcript", "transcripts", "messages", "chat_history", "cot",
        "chain_of_thought", "chainofthought", "chain-of-thought", "thought", "thoughts",
        "internal_dialogue", "internal-dialogue", "raw_responses", "reasoning",
        "reasoning_steps", "intermediate_steps", "intermediatesteps", "worker_log",
        "worker-log", "history", "content", "scratchpad"
    }

    @classmethod
    def _normalize_key(cls, k: str) -> str:
        return k.lower().replace("_", "").replace("-", "")

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """Sanitiza strings livres contra blocos de raciocínio / CoT explícitos."""
        if not text:
            return ""
        import re
        # Remove tags <thought>...</thought> ou <thinking>...</thinking>
        cleaned = re.sub(r"<(thought|thinking)>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
        return cleaned.strip()

    @classmethod
    def sanitize_dict(cls, data: Any) -> Any:
        """Filtra recursivamente chaves proibidas que possam conter CoT ou transcripts."""
        normalized_forbidden = {cls._normalize_key(k) for k in cls.FORBIDDEN_KEYS}
        if isinstance(data, dict):
            return {
                k: cls.sanitize_dict(v)
                for k, v in data.items()
                if cls._normalize_key(str(k)) not in normalized_forbidden
            }
        elif isinstance(data, list):
            return [cls.sanitize_dict(item) for item in data]
        elif isinstance(data, str):
            return cls.sanitize_text(data)
        return data

    @classmethod
    def build_reviewer_context(
        cls,
        spec: TaskSpec,
        artifacts: List[Dict[str, Any]],
        evidence: Dict[str, Any],
        residual_risk: List[str],
        summary: str,
        decisions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Monta contexto limpo e não enviesado para o revisor.
        """
        # Sanitiza artefatos e evidências contra vazamentos acidentais de transcripts
        clean_artifacts = cls.sanitize_dict(artifacts)
        clean_evidence = cls.sanitize_dict(evidence)
        clean_risks = [cls.sanitize_text(r) for r in residual_risk]
        clean_decisions = [cls.sanitize_text(d) for d in (decisions if decisions is not None else spec.context_decisions)]
        clean_summary = cls.sanitize_text(summary)

        # Critérios de aceitação da task
        acceptance_criteria = [
            {
                "id": c.id,
                "description": c.description,
                "type": c.type,
                "command": c.command,
            }
            for c in spec.acceptance_criteria
        ]

        return {
            "task_id": spec.id,
            "title": spec.title,
            "goal": spec.goal,
            "description": spec.description,
            "risk_level": spec.risk_level,
            "task_class": spec.task_class,
            "acceptance_criteria": acceptance_criteria,
            "artifacts": clean_artifacts,
            "evidence": clean_evidence,
            "residual_risk": clean_risks,
            "summary": clean_summary,
            "decisions": clean_decisions,
            "expected_artifacts": list(spec.expected_artifacts),
        }


class ReviewPipeline:
    """
    Executa os estágios de revisão definidos em `TaskSpec.review_stages`.
    Avalia condições `when` (como 'always', 'risk>=high', 'risk==critical').
    Gera vereditos formais `ReviewVerdict` para cada estágio acionado.
    """

    RISK_LEVELS = {
        "low": 10,
        "medium": 20,
        "high": 30,
        "critical": 40,
    }

    def __init__(
        self,
        stage_evaluator: Optional[Callable[[ReviewStage, Dict[str, Any]], ReviewVerdict]] = None,
    ):
        """
        stage_evaluator: Função que recebe (stage, reviewer_context) e retorna ReviewVerdict.
        Se None, utiliza avaliador padrão mock/heurístico.
        """
        self.stage_evaluator = stage_evaluator or self._default_evaluator

    def should_execute_stage(self, stage: ReviewStage, spec: TaskSpec, evidence: Optional[Dict[str, Any]] = None) -> bool:
        """Avalia a condição `when` de um ReviewStage contra a TaskSpec."""
        when = (stage.when or "always").strip().lower()

        if when in ("never", "false", "0", "disabled"):
            return False

        if when in ("always", "true", "1", "*"):
            return True

        task_risk = (spec.risk_level or "medium").strip().lower()
        task_risk_val = self.RISK_LEVELS.get(task_risk, 20)

        # Ex: "risk>=high", "risk>medium", "risk==critical", "risk<=low"
        if when.startswith("risk"):
            op_part = when[4:].strip()
            for op in [">=", "<=", "==", "!=", ">", "<"]:
                if op_part.startswith(op):
                    target_risk = op_part[len(op):].strip()
                    target_val = self.RISK_LEVELS.get(target_risk, 20)
                    if op == ">=":
                        return task_risk_val >= target_val
                    elif op == "<=":
                        return task_risk_val <= target_val
                    elif op == "==":
                        return task_risk_val == target_val
                    elif op == "!=":
                        return task_risk_val != target_val
                    elif op == ">":
                        return task_risk_val > target_val
                    elif op == "<":
                        return task_risk_val < target_val

        # Condições baseadas em evidências (ex: "tests_failed", "has_risks")
        if when == "tests_failed":
            tests_evidence = (evidence or {}).get("tests", {})
            return tests_evidence.get("failed", 0) > 0 or not tests_evidence.get("passed", True)
        if when == "has_risks":
            risks = (evidence or {}).get("residual_risk")
            return bool(risks)

        # Fallback defensivo: se não reconhecido, não pula silenciosamente se risco for alto
        return task_risk_val >= self.RISK_LEVELS["high"]

    def _default_evaluator(self, stage: ReviewStage, context: Dict[str, Any]) -> ReviewVerdict:
        """Avaliador padrão baseado nas evidências e riscos residuais presentes no contexto."""
        evidence = context.get("evidence", {})
        residual_risk = context.get("residual_risk", [])
        
        # Se houver evidência de teste falhado ou lsp com erros novos, rejeita
        lsp = evidence.get("lsp", {})
        tests = evidence.get("tests", {})

        approved = True
        rationale_parts = []
        score = 1.0

        if lsp.get("new_errors", 0) > 0:
            approved = False
            rationale_parts.append(f"LSP reported {lsp['new_errors']} new errors")
            score -= 0.3

        if tests.get("failed", 0) > 0:
            approved = False
            rationale_parts.append(f"Test suite reported {tests['failed']} failed tests")
            score -= 0.4

        if residual_risk:
            score -= min(0.3, len(residual_risk) * 0.1)
            rationale_parts.append(f"Identified {len(residual_risk)} residual risk(s)")

        if not rationale_parts:
            rationale_parts.append(f"Stage {stage.id} approved automatically based on clean evidence.")

        score = max(0.0, min(1.0, score))
        if score < 0.8:
            approved = False

        return ReviewVerdict(
            stage_id=stage.id,
            posture=stage.posture,
            approved=approved,
            rationale="; ".join(rationale_parts),
            score=score,
        )

    def run(
        self,
        spec: TaskSpec,
        artifacts: List[Dict[str, Any]],
        evidence: Dict[str, Any],
        residual_risk: List[str],
        summary: str,
        decisions: Optional[List[str]] = None,
    ) -> List[ReviewVerdict]:
        """
        Executa a esteira de revisão para todos os estágios aplicáveis em `spec.review_stages`.
        """
        # Garante que residual_risk também esteja acessível dentro de evidence para should_execute_stage
        combined_evidence = dict(evidence or {})
        if residual_risk and "residual_risk" not in combined_evidence:
            combined_evidence["residual_risk"] = list(residual_risk)

        context = AntiAnchoringContextBuilder.build_reviewer_context(
            spec=spec,
            artifacts=artifacts,
            evidence=combined_evidence,
            residual_risk=residual_risk,
            summary=summary,
            decisions=decisions,
        )

        verdicts: List[ReviewVerdict] = []
        for stage in spec.review_stages:
            if self.should_execute_stage(stage, spec, combined_evidence):
                verdict = self.stage_evaluator(stage, context)
                verdicts.append(verdict)

        return verdicts
