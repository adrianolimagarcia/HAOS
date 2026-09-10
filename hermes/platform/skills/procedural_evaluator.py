"""Avaliador determinístico do gate de promoção de skills procedurais.

Por que existe: ``SkillLifecyclePipeline.evaluate_and_activate`` compara a nota da
skill com ``min_eval_score``, mas sem runner registrado caía em
``spec.eval_score if spec.eval_score is not None else 1.0`` — ou seja, promovia
qualquer coisa com nota 1.0. O portão que decide o que o agente aprende não media
nada, e nenhum runner era registrado em produção (só um mock em teste).

Este é o runner de produção. Ele mede o que é verificável **offline, sem rede e
sem LLM**: identidade, concisão/qualidade da descrição, semver, atribuição,
capacidades declaradas e — o check decisivo — se o conteúdo executável da skill
realmente compila. ``score`` é a fração de checks aprovados (medida, não
declarada); o limite de aprovação continua sendo política do pipeline
(``min_eval_score``), não deste módulo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

from hermes.platform.skills.procedural_engine import EvalTestResult, parse_semver
from hermes.platform.skills.spec import SkillSpec

# Termos de marketing proibidos pelo padrão de autoria (skills/AGENTS.md, regra 1):
# descrevem venda, não capacidade, e incham a listagem.
_MARKETING_TERMS: Tuple[str, ...] = (
    "powerful", "comprehensive", "seamless", "advanced", "robust",
    "cutting-edge", "state-of-the-art", "revolutionary", "best-in-class",
)
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MAX_DESCRIPTION_CHARS = 60


@dataclass
class SkillCheck:
    """Um check determinístico: id estável, veredito e a evidência do veredito."""

    check_id: str
    passed: bool
    detail: str = ""
    critical: bool = False


class DeterministicSkillEvaluator:
    """Runner ``Callable[[SkillSpec], EvalTestResult]`` para o pipeline de skills.

    ``score`` = checks aprovados / total (0.0–1.0). ``passed`` = nenhum check
    crítico falhou: um check de qualidade reprovado derruba a nota, mas só um
    defeito estrutural (nome inválido, script que não compila) barra a skill antes
    do gate de score — que é quem decide a promoção.
    """

    def __init__(self, max_description_chars: int = MAX_DESCRIPTION_CHARS) -> None:
        self.max_description_chars = max_description_chars

    # ------------------------------------------------------------------ #
    # checks
    # ------------------------------------------------------------------ #
    def check_identity(self, spec: SkillSpec) -> SkillCheck:
        name = (spec.name or "").strip()
        if not name:
            return SkillCheck("identity", False, "nome vazio", critical=True)
        if not _NAME_RE.match(name):
            return SkillCheck(
                "identity", False,
                f"nome '{name}' fora do padrão (minúsculas, dígitos, '.', '_', '-')",
                critical=True,
            )
        return SkillCheck("identity", True, f"nome '{name}' canônico", critical=True)

    def check_description(self, spec: SkillSpec) -> SkillCheck:
        desc = (spec.description or "").strip()
        if not desc:
            return SkillCheck("description_concision", False, "descrição vazia")
        problems: List[str] = []
        if len(desc) > self.max_description_chars:
            problems.append(f"{len(desc)} chars > {self.max_description_chars}")
        if not desc.endswith("."):
            problems.append("não termina em ponto final")
        lowered = desc.lower()
        found = [t for t in _MARKETING_TERMS if t in lowered]
        if found:
            problems.append(f"termo(s) de marketing: {', '.join(found)}")
        if problems:
            return SkillCheck("description_concision", False, "; ".join(problems))
        return SkillCheck("description_concision", True, f"{len(desc)} chars, sem marketing")

    def check_semver(self, spec: SkillSpec) -> SkillCheck:
        try:
            parse_semver(spec.version)
        except (ValueError, TypeError) as exc:
            return SkillCheck("version_semver", False, f"versão inválida: {exc}")
        return SkillCheck("version_semver", True, f"v{spec.version}")

    def check_attribution(self, spec: SkillSpec) -> SkillCheck:
        missing = [f for f in ("author", "license") if not (getattr(spec, f) or "").strip()]
        if missing:
            return SkillCheck("attribution", False, f"campos ausentes: {', '.join(missing)}")
        return SkillCheck("attribution", True, f"{spec.author} / {spec.license}")

    def check_capabilities(self, spec: SkillSpec) -> SkillCheck:
        caps = [c for c in (spec.capabilities_required or []) if str(c).strip()]
        if not caps:
            return SkillCheck(
                "capabilities_declared", False,
                "nenhuma capability declarada: a skill não é resolvível nem auditável",
            )
        return SkillCheck("capabilities_declared", True, ", ".join(sorted(caps)))

    def check_executable(self, spec: SkillSpec) -> SkillCheck:
        script = spec.entry_script
        if not script or not script.strip():
            return SkillCheck(
                "executable_evidence", False,
                "sem conteúdo executável: nada a medir além de metadados",
                critical=True,
            )
        try:
            compile(script, f"<skill:{spec.name}>", "exec")
        except SyntaxError as exc:
            return SkillCheck(
                "executable_evidence", False,
                f"script não compila: {exc.msg} (linha {exc.lineno})",
                critical=True,
            )
        return SkillCheck("executable_evidence", True, "conteúdo executável compila", critical=True)

    def checks(self, spec: SkillSpec) -> List[SkillCheck]:
        """Todos os checks, na ordem estável em que entram na nota."""
        return [
            self.check_identity(spec),
            self.check_executable(spec),
            self.check_description(spec),
            self.check_semver(spec),
            self.check_attribution(spec),
            self.check_capabilities(spec),
        ]

    # ------------------------------------------------------------------ #
    # contrato do pipeline
    # ------------------------------------------------------------------ #
    def __call__(self, spec: SkillSpec) -> EvalTestResult:
        results = self.checks(spec)
        failed = [c for c in results if not c.passed]
        score = (len(results) - len(failed)) / len(results) if results else 0.0
        blocked = [c for c in failed if c.critical]
        passed = not blocked

        if blocked:
            message = "check crítico reprovado: " + "; ".join(
                f"{c.check_id} ({c.detail})" for c in blocked
            )
        elif failed:
            message = "checks reprovados: " + "; ".join(
                f"{c.check_id} ({c.detail})" for c in failed
            )
        else:
            message = f"{len(results)}/{len(results)} checks aprovados"

        return EvalTestResult(
            test_id="deterministic-skill-evaluator",
            passed=passed,
            score=score,
            message=message,
        )


def build_evaluated_pipeline(registry, **kwargs):
    """Pipeline de promoção com o avaliador determinístico já registrado.

    Existe para que nenhum call site de produção repita (e possa esquecer) o
    registro do runner — que era exatamente o defeito: pipeline sem avaliador.
    """
    from hermes.platform.skills.procedural_engine import SkillLifecyclePipeline

    pipeline = SkillLifecyclePipeline(registry=registry, **kwargs)
    pipeline.register_test_runner(DeterministicSkillEvaluator())
    return pipeline
