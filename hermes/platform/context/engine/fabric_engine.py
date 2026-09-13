"""FabricContextEngine — Integração com ContextEngine do Hermes upstream.

Implementa:
- select_context(): Hook per-turn chamado antes de cada request ao provider para injetar
  o ContextPackage estruturado sem mutar o transcript persistente em disco.
- on_turn_complete(): Hook para pós-processamento de turno e auditoria.
- Preserva prompt caching upstream através de stable prefix.
- Delega compressão e token counting ao ContextEngine upstream quando necessário.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

# Herança direta do ContextEngine do Hermes upstream
from agent.context_engine import ContextEngine
from hermes.platform.context.budget.budgeter import ContextBudgeter
from hermes.platform.context.memory.provider import HermesFabricMemoryProvider
from hermes.platform.context.policies.policy import ContextPolicy, resolve_context_policy
from hermes.platform.context.primitives.item import ContextItem
from hermes.platform.context.primitives.package import ContextPackage
from hermes.platform.context.retrieval.router import RetrievalRouter

logger = logging.getLogger(__name__)


class FabricContextEngine(ContextEngine):
    """Engine de contexto federado do HAOS conectado ao loop do Hermes Agent."""

    @property
    def name(self) -> str:
        return "fabric"

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        """Atualiza a contagem de tokens com base na resposta."""
        if not usage:
            return
        self.last_prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        self.last_completion_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
        self.last_total_tokens = usage.get("total_tokens", self.last_prompt_tokens + self.last_completion_tokens)
        if self.upstream_compressor and hasattr(self.upstream_compressor, "update_from_response"):
            self.upstream_compressor.update_from_response(usage)

    def should_compress(self, prompt_tokens: Optional[int] = None) -> bool:
        """Determina se deve comprimir delegando ao upstream se configurado."""
        if self.upstream_compressor and hasattr(self.upstream_compressor, "should_compress"):
            return self.upstream_compressor.should_compress(prompt_tokens)
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        if self.threshold_tokens > 0:
            return tokens >= self.threshold_tokens
        return False

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
        force: bool = False,
        memory_context: str = "",
    ) -> List[Dict[str, Any]]:
        """Compacta mensagens delegando ao upstream_compressor quando disponível."""
        if self.upstream_compressor and hasattr(self.upstream_compressor, "compress"):
            return self.upstream_compressor.compress(
                messages,
                current_tokens=current_tokens,
                focus_topic=focus_topic,
                force=force,
                memory_context=memory_context,
            )
        return messages

    def __init__(
        self,
        posture_name: str = "coder",
        token_budget_limit: int = 64000,
        policy: Optional[ContextPolicy] = None,
        upstream_compressor: Optional[Any] = None,
        memory_provider: Optional[HermesFabricMemoryProvider] = None,
    ):
        self.posture_name = posture_name
        self.policy = policy or resolve_context_policy(posture_name, max_tokens=token_budget_limit)
        self.budgeter = ContextBudgeter(self.policy, total_budget=token_budget_limit)
        self.upstream_compressor = upstream_compressor
        self.memory_provider = memory_provider or HermesFabricMemoryProvider()
        self.router = RetrievalRouter()

        # Conecta os adapters de memória como fontes no RetrievalRouter
        self.router.register_source(self.memory_provider.obsidian)
        self.router.register_source(self.memory_provider.decisions)
        self.router.register_source(self.memory_provider.graphrag)

        self._current_package: Optional[ContextPackage] = None
        self._sources_data: Dict[str, List[ContextItem]] = {}
        self._turn_count: int = 0

    @property
    def current_tokens(self) -> int:
        """Contagem atual estimada de tokens do contexto montado."""
        if self._current_package:
            return self._current_package.token_count
        if self.upstream_compressor and hasattr(self.upstream_compressor, "current_tokens"):
            return self.upstream_compressor.current_tokens
        return 0

    def register_section_items(self, section: str, items: List[ContextItem]) -> None:
        """Registra itens coletados das fontes em seções estruturadas."""
        if section not in self._sources_data:
            self._sources_data[section] = []
        self._sources_data[section].extend(items)

    def compile_context(
        self,
        task_id: str,
        task_revision: int = 1,
        task_signals: Optional[List[str]] = None,
    ) -> ContextPackage:
        """Compila o ContextPackage aplicando a política de postura e orçamento."""
        self._current_package = self.budgeter.fit_package(
            task_id=task_id,
            task_revision=task_revision,
            candidates_by_section=self._sources_data,
            task_signals=task_signals,
        )
        return self._current_package

    def select_context(
        self,
        request_messages: List[Dict[str, Any]],
        *,
        conversation_messages: Optional[List[Dict[str, Any]]] = None,
        incoming_message: Optional[Dict[str, Any]] = None,
        budget_tokens: int = 0,
    ) -> Optional[List[Dict[str, Any]]]:
        """Hook canônico do upstream Hermes chamado antes de cada chamada ao provider.
        
        Monta a seleção pontual de mensagens sem alterar o transcript original.
        Preserva rigorosamente a alternância de papéis e o prompt cache.
        """
        if not self._current_package:
            # Retorna None para deixar as request_messages inalteradas no upstream
            return None

        # Clona as mensagens para a requisição corrente
        selected_messages = [copy.deepcopy(m) for m in request_messages]

        # 1. Monta bloco estável de contexto compilado
        stable_prefix = self._current_package.render_stable_prefix()
        semi_stable = self._current_package.render_semi_stable_body()

        compiled_context_block = (
            f"=== HAOS CONTEXT FABRIC (POSTURE: {self.posture_name.upper()}) ===\n"
            f"{stable_prefix}\n\n"
            f"{semi_stable}\n"
            f"=== END CONTEXT FABRIC ==="
        )

        # 2. Injeta no System Message existente ou cria um novo no início
        has_system = False
        for msg in selected_messages:
            if msg.get("role") == "system":
                msg["content"] = (msg.get("content") or "") + "\n\n" + compiled_context_block
                has_system = True
                break

        if not has_system:
            selected_messages.insert(0, {
                "role": "system",
                "content": compiled_context_block,
            })

        return selected_messages

    def on_turn_complete(
        self,
        messages: List[Dict[str, Any]],
        usage: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Hook canônico do upstream Hermes após o modelo concluir seu turno."""
        self._turn_count += 1
        logger.debug(
            "FabricContextEngine turn complete (turn count: %s, msgs: %s)",
            self._turn_count,
            len(messages),
        )
        if self.upstream_compressor and hasattr(self.upstream_compressor, "on_turn_complete"):
            self.upstream_compressor.on_turn_complete(
                messages=messages,
                usage=usage,
                **kwargs,
            )

    def on_session_reset(self) -> None:
        """Limpa o estado temporário do run mantendo configurações intactas."""
        self._turn_count = 0
        self._current_package = None
        self._sources_data.clear()
        if self.upstream_compressor and hasattr(self.upstream_compressor, "on_session_reset"):
            self.upstream_compressor.on_session_reset()
