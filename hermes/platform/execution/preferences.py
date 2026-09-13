"""Emendas 8/9 — agentes e modelo required vs preferred (funções puras).

Semântica documentada no TaskSpec:
- ``required_agents``: allow-list OBRIGATÓRIA de agentes/lanes. Vazio = qualquer
  candidato; não-vazio = candidato fora da lista NÃO é elegível (fail-fast).
- ``preferred_agents``: ordena candidatos ELEGÍVEIS (nunca exclui; só desempata
  a escolha entre os que já passaram pelo required).
- ``model_profile_preferred``: perfil SOFT que só entra quando NÃO há binding
  required (``model_profile``) nem postura com perfil próprio — nunca
  sobrescreve required/postura (precedência v1.1 preservada).
"""

from typing import List, Optional, Sequence, Tuple


def agent_eligibility(
    required_agents: Sequence[str],
    preferred_agents: Sequence[str],
    candidate: str,
) -> Tuple[bool, bool]:
    """(elegível, preferido) de um candidato.

    Elegível = requeridos vazios OU candidato ∈ requeridos. Preferido =
    elegível E candidato ∈ preferidos (ordem dos preferidos não exclui)."""
    required = list(required_agents or [])
    preferred = list(preferred_agents or [])
    eligible = not required or candidate in required
    if not eligible:
        return False, False
    return True, candidate in preferred


def select_agent_candidate(
    required_agents: Sequence[str],
    preferred_agents: Sequence[str],
    candidates: Sequence[str],
) -> Optional[str]:
    """Escolhe UM candidato: primeiro entre os preferidos elegíveis (na ordem
    dos preferidos), senão o primeiro candidato elegível na ordem dada.

    Nenhum candidato elegível -> None."""
    eligible = [
        c for c in candidates
        if agent_eligibility(required_agents, preferred_agents, c)[0]
    ]
    if not eligible:
        return None
    for pref in preferred_agents:
        if pref in eligible:
            return pref
    return eligible[0]


def resolve_model_profile(
    required: Optional[str],
    posture_default: Optional[str],
    preferred: Optional[str],
) -> Tuple[Optional[str], str]:
    """(profile_id, binding_level) da precedência v1.1.

    Níveis: "required" > "posture" > "preferred" > "unbound". O preferred só é
    usado quando required e postura não resolveram nada (soft de verdade)."""
    if required:
        return required, "required"
    if posture_default:
        return posture_default, "posture"
    if preferred:
        return preferred, "preferred"
    return None, "unbound"
