"""ContextSource — Interface abstrata para todas as fontes de dados do Context Fabric.

Toda fonte retorna uma lista de ContextItem com proveniência, tipo e confiança declarados.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from hermes.platform.context.primitives.item import ContextItem


class ContextSource(ABC):
    """Interface base para provedores de contexto federados."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Nome identificador da fonte (ex.: 'task', 'artifacts', 'lsp', 'obsidian', 'git')."""
        pass

    @abstractmethod
    def retrieve(
        self,
        query: str,
        task_id: str,
        budget_hint: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ContextItem]:
        """Recupera itens de contexto relevantes da fonte sob demanda."""
        pass
