"""Evolution Ledger — Ouroboros com decisão (delta 44, P1).

O K7 (analyzer) calcula proposals a partir de dados reais, mas em
``mode="PROPOSAL_ONLY"`` elas MORREM no relatório: ninguém decide, nada fica
registrado. O delta 44 fecha o loop no lado da DECISÃO (nunca aplicação
automática — shadow mode continua):

* ``submit(proposal)`` — persiste a proposta como evento
  ``evolution.proposal.submitted`` no EventStore (append-only real), com um
  ``proposal_id`` determinístico (hash do payload) para idempotência: a mesma
  proposta não entra duas vezes.
* ``pending()`` — propostas submetidas sem decisão (derivada do stream, não
  segundo estado).
* ``decide(proposal_id, verdict, approver, rationale)`` — grava
  ``evolution.proposal.decided`` (verdict approved|rejected + approver +
  rationale + evidência). Decidir proposta inexistente/indecidível lança
  ``LedgerError`` (fail-closed).
* ``history()`` — stream completo (submitted + decided) ordenado.

Persistência: o próprio EventStore (Event já tem event_id/timestamp); o
ledger NÃO cria segundo store. Eventos com nome ``evolution.proposal.*``.

Regra de ouro (shadow mode): aprovar uma proposta NÃO aplica nada
automaticamente no runtime — registra a decisão para o control plane
humano/UI (DashboardStats.evolution_pending). Aplicar mudança de perfil
continua decisão explícita fora deste módulo.
"""

import hashlib
import json
from typing import Any, Dict, List, Optional

from hermes.platform.observability.events import Event


class LedgerError(RuntimeError):
    """Erro do ledger de evolução (fail-closed)."""


_PROPOSAL_SUBMITTED = "evolution.proposal.submitted"
_PROPOSAL_DECIDED = "evolution.proposal.decided"
_VALID_VERDICTS = ("approved", "rejected")


def _stable_id(payload: Dict[str, Any]) -> str:
    """Id determinístico: mesma proposta -> mesmo id (idempotência)."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class EvolutionLedger:
    """Ledger de propostas do Ouroboros sobre o EventStore (append-only)."""

    def __init__(self, event_store):
        self._store = event_store

    # ------------------------------------------------------------------ #
    # submit
    # ------------------------------------------------------------------ #
    def submit(self, proposal: Dict[str, Any]) -> str:
        """Persiste a proposta (idempotente) e devolve o proposal_id."""
        if not isinstance(proposal, dict):
            raise LedgerError("submit requer dict de proposta")
        proposal_id = proposal.get("proposal_id") or _stable_id(proposal)
        if self._has_submitted(proposal_id):
            return proposal_id  # já registrada: não duplica no stream
        payload = dict(proposal)
        payload["proposal_id"] = proposal_id
        self._store.append(Event(name=_PROPOSAL_SUBMITTED, payload=payload))
        return proposal_id

    # ------------------------------------------------------------------ #
    # decision
    # ------------------------------------------------------------------ #
    def decide(
        self,
        proposal_id: str,
        verdict: str,
        approver: str,
        rationale: Optional[str] = None,
    ) -> str:
        """Registra a decisão sobre uma proposta submetida (approved/rejected).

        Fail-closed: proposta inexistente, já decidida ou veredito inválido
        lança ``LedgerError`` — não há decisão fantasma nem sobrescrita.
        """
        if verdict not in _VALID_VERDICTS:
            raise LedgerError(f"veredito inválido: {verdict!r} (approved|rejected)")
        submitted = self._find_submitted(proposal_id)
        if submitted is None:
            raise LedgerError(f"proposta inexistente: {proposal_id}")
        if self._find_decision(proposal_id) is not None:
            raise LedgerError(f"proposta já decidida: {proposal_id}")
        if not isinstance(approver, str) or not approver:
            raise LedgerError("decide requer approver string não vazia")
        self._store.append(Event(name=_PROPOSAL_DECIDED, payload={
            "proposal_id": proposal_id,
            "verdict": verdict,
            "approver": approver,
            "rationale": rationale,
            "evidence": submitted.get("payload", {}).get("evidence"),
        }))
        return proposal_id

    # ------------------------------------------------------------------ #
    # queries (derivadas do stream — sem estado próprio)
    # ------------------------------------------------------------------ #
    def pending(self) -> List[Dict[str, Any]]:
        """Propostas submetidas e ainda sem decisão (fail-safe: [] sem dado)."""
        decided_ids = {d["payload"]["proposal_id"] for d in self._events(_PROPOSAL_DECIDED)}
        return [
            e["payload"] for e in self._events(_PROPOSAL_SUBMITTED)
            if e["payload"].get("proposal_id") not in decided_ids
        ]

    def history(self) -> List[Dict[str, Any]]:
        """Stream completo ordenado: submitted + decided (event_id/timestamp)."""
        return self._events()  # EventStore já devolve em ordem de append

    def _events(self, name: Optional[str] = None) -> List[Dict[str, Any]]:
        events = self._store.get_all(name=name)
        return [e.to_dict() for e in events]

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _has_submitted(self, proposal_id: str) -> bool:
        return self._find_submitted(proposal_id) is not None

    def _find_submitted(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        for e in self._events(_PROPOSAL_SUBMITTED):
            if e["payload"].get("proposal_id") == proposal_id:
                return e
        return None

    def _find_decision(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        for e in self._events(_PROPOSAL_DECIDED):
            if e["payload"].get("proposal_id") == proposal_id:
                return e
        return None


__all__ = ["EvolutionLedger", "LedgerError"]
