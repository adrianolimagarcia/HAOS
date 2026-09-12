"""Verificação antes de mais agentes (backlog HAOS P9 — Etapa 4).

Interpretação documentada (a spec só diz "verificação antes de mais agentes";
objetivo detalhado inexistente in-repo): um GATE de verificação antes de
delegar/escalar — antes de GERAR novos agentes, o pai não pode embarcar
trabalho não verificado. O gate é ADITIVO aos caps 3/2 (não-objetivo §11:
não remover): ele nunca afrouxa limite de profundidade/concorrência; só pode
RECUSAR um spawn que já passaria nos caps.

Estado que o gate lê (tudo já existe no loop de turno, nada de infra nova):
- ``agent._turn_file_mutation_paths`` — paths que as ferramentas de mutação
  de arquivo gravaram NESTE turno (turn_explainers.py popula; turn_context.py
  zera por turno): a evidência de "edição não verificada";
- ``agent._work_verified`` — atestação de verificação (canal explícito para
  quem tem evidência de verificação; nenhum escritor default é inventado —
  a evidência concreta de verificação ficaria fora do contrato mínimo).

Config: ``delegation.require_verification_before_spawn`` (bool, default
False = comportamento atual 100% intacto). Quando True (fail-closed):
- pai atesta ``_work_verified`` (ou ``verified=True`` explícito) → libera;
- pai com edições não verificadas no turno → RECUSA com mensagem clara;
- sem edições → libera (nada a verificar).
"""

from __future__ import annotations

from typing import Any, Optional, Tuple


def verification_spawn_gate(
    *,
    verified: Optional[bool],
    unverified_edits: bool,
    require: bool,
) -> Tuple[bool, str]:
    """Decisão pura do gate (P9). Retorna ``(allowed, message)``.

    - ``require=False`` → sempre libera (comportamento atual);
    - ``verified=True`` → libera (atestação explícita);
    - ``unverified_edits=True`` → RECUSA fail-closed;
    - senão → libera (nada a verificar).
    Determinística: mesma entrada, mesma decisão.
    """
    if not require:
        return True, ""
    if verified:
        return True, ""
    if unverified_edits:
        return False, (
            "Delegation refused by the verification gate: the parent turn has "
            "unverified changes (file edits without verification evidence). "
            "Verify the current work before spawning more agents "
            "(delegation.require_verification_before_spawn is enabled)."
        )
    return True, ""


def _get_require_verification_before_spawn() -> bool:
    """``delegation.require_verification_before_spawn`` (default False)."""
    from tools.delegate_tool_config import _cfg

    cfg = _cfg()
    return bool(isinstance(cfg, dict) and cfg.get("require_verification_before_spawn") is True)


def spawn_verification_message(parent_agent: Any) -> Optional[str]:
    """None = libera o spawn; string = mensagem de recusa (fail-closed).

    Lê a config + o estado do pai (edições do turno, atestação). Sem o knob
    ligado, retorna sempre None (zero impacto no fluxo atual).
    """
    if not _get_require_verification_before_spawn():
        return None
    verified = bool(getattr(parent_agent, "_work_verified", False))
    edits = set(getattr(parent_agent, "_turn_file_mutation_paths", set()) or set())
    allowed, message = verification_spawn_gate(
        verified=verified, unverified_edits=bool(edits), require=True,
    )
    return None if allowed else message
