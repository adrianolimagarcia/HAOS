"""Ouroboros Recursive Self-Improvement (RSI) Engine.

Inspirado nos princípios do artigo arXiv:2609.11873:
- Dual-Loop: Loop 1 (tarefas) -> Loop 2 (Meta-aprimoramento de skills e heurísticas).
- Blind Independent Validation: Avaliação desacoplada pelo Gemini 2.5 Flash Lite (8790).
- Persistent Skill Crystallization: Síntese de SKILL.md em ~/.haos/skills/evolution/<bot_id>/.
"""

import collections
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home
from hermes.platform.decision import get_decision_engine

logger = logging.getLogger(__name__)


class OuroborosRSILoop:
    """Motor de Auto-Aprimoramento Recursivo desacoplado."""

    MIN_FAILURE_CLUSTER: int = 3

    def __init__(self, skills_root: Optional[Path] = None):
        self.skills_root = skills_root or (get_hermes_home() / "skills" / "evolution")
        self.skills_root.mkdir(parents=True, exist_ok=True)
        self.decision_engine = get_decision_engine()
        # Histórico em memória de falhas agregadas por bot
        self._failures_by_bot: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)

    def record_task_failure(
        self,
        bot_id: str,
        task_id: str,
        task_name: str,
        error_message: str,
        failure_category: str = "execution_error",
    ) -> Optional[Dict[str, Any]]:
        """Registra uma falha de tarefa.

        Se o bot acumular MIN_FAILURE_CLUSTER falhas, dispara o Meta-Loop para
        sintetizar ou atualizar uma Meta-Skill corretiva com validação independente.
        """
        incident = {
            "task_id": task_id,
            "task_name": task_name,
            "error_message": error_message,
            "category": failure_category,
            "timestamp": time.time(),
        }
        self._failures_by_bot[bot_id].append(incident)

        bot_failures = self._failures_by_bot[bot_id]
        if len(bot_failures) >= self.MIN_FAILURE_CLUSTER:
            # Dispara Meta-Loop recursivo
            recent_failures = bot_failures[-self.MIN_FAILURE_CLUSTER:]
            result = self.synthesize_meta_skill(bot_id, recent_failures)
            # Limpa o cluster consumido
            self._failures_by_bot[bot_id] = []
            return result
        return None

    def synthesize_meta_skill(
        self,
        bot_id: str,
        failure_cluster: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Meta-Loop: Analisa falhas recorrentes, sintetiza SKILL.md e valida cegamente."""
        category = failure_cluster[0].get("category", "general")
        errors_summary = "\n".join(
            f"- Tarefa '{f.get('task_name')}': {f.get('error_message')[:160]}"
            for f in failure_cluster
        )

        skill_name = f"resilience-{category.lower().replace('_', '-')}"
        # Aplicação da Regra de Ouro do Delta Memory (Volodymyr Vreshch, 2026):
        # A memória do agente não é o que ele já sabe (boas práticas óbvias), mas apenas o delta
        # (invariantes específicas deste appliance, workarounds concretos e checagens estritas).
        prompt = (
            f"O agente/bot '{bot_id}' falhou repetidamente com os seguintes erros reais:\n"
            f"{errors_summary}\n\n"
            f"DIRETIVA DE DELTA MEMORY (Anti-Ruído):\n"
            f"NÃO gere obviedades de programação ou conselhos genéricos ('use try/except', 'valide entradas').\n"
            f"Concentre-se exclusivamente no DELTA: o workaround concreto, o comando exato, os caminhos ou as checagens prévias "
            f"necessárias para neutralizar esse padrão de erro específico neste ambiente HAOS.\n\n"
            f"Elabore uma diretriz técnica rigorosa (SKILL.md) estruturada."
        )

        schema = (
            '{"skill_title": string, "mitigation_rules": [string], "skill_markdown": string}'
        )

        try:
            # 1. Geração de Hipótese de Mitigação pelo Gemini Lite
            raw = self.decision_engine._call_gemini_lite(prompt, schema)
            skill_md = raw.get("skill_markdown") or (
                f"# Skill de Resiliência: {category}\n\n"
                f"## Problemas Detectados\n{errors_summary}\n\n"
                f"## Regras Mandatórias (Delta Específico)\n"
                + "\n".join(f"- {r}" for r in raw.get("mitigation_rules", []))
            )

            # 2. Blind Independent Validation (Princípio do Paper arXiv:2609.11873 + Delta Guard)
            # Um juiz independente avalia se a skill tem delta real e não apenas obviedades genéricas
            validation_context = f"Erros originais:\n{errors_summary}\n\nConteúdo da Skill gerada:\n{skill_md}"
            val_result = self.decision_engine.decide_boolean(
                statement=(
                    "Does this corrective skill provide an actionable, environment-specific delta/workaround "
                    "that directly mitigates the failure pattern without being generic platitudes or adding invalid side effects?"
                ),
                context=validation_context,
                domain="meta_evolution_validation",
            )

            if not val_result.verdict:
                logger.warning(
                    "Ouroboros RSI: Meta-Skill rejeitada na validação independente para bot %s: %s",
                    bot_id, val_result.reason
                )
                return {
                    "ok": False,
                    "bot_id": bot_id,
                    "status": "rejected_by_validator",
                    "reason": val_result.reason,
                    "confidence": val_result.confidence,
                }

            # 3. Cristalização Persistente em Disco
            bot_skill_dir = self.skills_root / bot_id / skill_name
            bot_skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file = bot_skill_dir / "SKILL.md"
            skill_file.write_text(skill_md, encoding="utf-8")

            logger.info("Ouroboros RSI: Meta-Skill cristalizada com sucesso em %s", skill_file)

            return {
                "ok": True,
                "bot_id": bot_id,
                "status": "promoted",
                "skill_name": skill_name,
                "skill_path": str(skill_file),
                "validator_verdict": val_result.verdict,
                "validator_confidence": val_result.confidence,
                "validator_reason": val_result.reason,
                "created_at": time.time(),
            }
        except Exception as exc:
            logger.error("Ouroboros RSI synthesis failed: %s", exc, exc_info=True)
            return {"ok": False, "bot_id": bot_id, "error": str(exc)}

    def list_crystallized_skills(self) -> List[Dict[str, Any]]:
        """Lista todas as Meta-Skills geradas recursivamente pelo Ouroboros."""
        items = []
        if not self.skills_root.exists():
            return items

        for bot_dir in self.skills_root.iterdir():
            if bot_dir.is_dir():
                for skill_dir in bot_dir.iterdir():
                    if skill_dir.is_dir():
                        s_file = skill_dir / "SKILL.md"
                        if s_file.exists():
                            items.append({
                                "bot_id": bot_dir.name,
                                "skill_name": skill_dir.name,
                                "path": str(s_file),
                                "modified_at": s_file.stat().st_mtime,
                                "size_bytes": s_file.stat().st_size,
                            })
        return items


# Singleton global
_rsi_loop_instance: Optional[OuroborosRSILoop] = None


def get_ouroboros_rsi() -> OuroborosRSILoop:
    global _rsi_loop_instance
    if _rsi_loop_instance is None:
        _rsi_loop_instance = OuroborosRSILoop()
    return _rsi_loop_instance
