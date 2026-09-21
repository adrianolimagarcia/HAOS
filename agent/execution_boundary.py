"""HAOS Execution Boundary & Anti-Looping Guard:
Inspirado nos conceitos do agent-harness (Paolo Perrone).

Intercepta chamadas de ferramentas antes do despacho e monitora recusas consecutivas.
Se o modelo persistir tentando o mesmo comando bloqueado (ou com os mesmos argumentos),
o BoundaryGuard altera a mensagem de recusa para um bloqueio categórico inegociável,
forçando o LLM a mudar de estratégia e quebrando loops de alucinação de retry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class Denied(str):
    """Representa uma recusa de execução pelo harness com identificação da regra violada."""

    rule: str
    is_terminal: bool

    def __new__(cls, text: str, rule: str = "", is_terminal: bool = False) -> "Denied":
        obj = super().__new__(cls, text)
        obj.rule = rule
        obj.is_terminal = is_terminal
        return obj


class ExecutionBoundaryGuard:
    """Rastreador de execução por sessão que quebra loops de retry contra regras determinísticas."""

    def __init__(self, max_consecutive_denials: int = 3) -> None:
        self.max_consecutive_denials = max_consecutive_denials
        self._lock = threading.Lock()
        # session_id -> {"last_call_hash": str, "count": int, "rule": str}
        self._session_state: Dict[str, Dict[str, Any]] = {}

    def _hash_call(self, tool_name: str, args: Dict[str, Any]) -> str:
        try:
            normalized = json.dumps({"tool": tool_name, "args": args}, sort_keys=True, default=str)
        except Exception:
            normalized = f"{tool_name}:{str(args)}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def record_denial(
        self, session_id: str, tool_name: str, args: Dict[str, Any], rule_name: str, reason: str
    ) -> Denied:
        """Registra uma recusa e retorna um objeto Denied com mensagem adaptativa."""
        call_hash = self._hash_call(tool_name, args)

        with self._lock:
            state = self._session_state.setdefault(
                session_id, {"last_call_hash": None, "count": 0, "rule": None}
            )

            if state["last_call_hash"] == call_hash and state["rule"] == rule_name:
                state["count"] += 1
            else:
                state["last_call_hash"] = call_hash
                state["rule"] = rule_name
                state["count"] = 1

            count = state["count"]

            if count >= self.max_consecutive_denials:
                msg = (
                    f"DENIED by boundary guard [{rule_name}] (attempt {count}/{self.max_consecutive_denials}). "
                    f"This operation has been refused repeatedly with the same arguments. "
                    f"The policy will NOT change. Do not retry this tool call; adopt an alternate plan."
                )
                logger.warning(
                    "[BOUNDARY-BREAKER] Session %s tripped anti-loop on rule %s (%d attempts)",
                    session_id,
                    rule_name,
                    count,
                )
                return Denied(msg, rule=rule_name, is_terminal=True)

            return Denied(
                f"DENIED by boundary guard [{rule_name}]: {reason}",
                rule=rule_name,
                is_terminal=False,
            )

    def record_success(self, session_id: str) -> None:
        """Reseta o rastreador de negações consecutivas após uma operação válida."""
        with self._lock:
            if session_id in self._session_state:
                self._session_state[session_id]["count"] = 0
                self._session_state[session_id]["last_call_hash"] = None
                self._session_state[session_id]["rule"] = None

    def clear_session(self, session_id: str) -> None:
        """Limpa o estado da sessão ao ser encerrada."""
        with self._lock:
            self._session_state.pop(session_id, None)


# Instância singleton global do boundary guard
boundary_guard = ExecutionBoundaryGuard(max_consecutive_denials=3)
