"""LSPSource — ContextSource determinístico para inteligência de código.

Consulta símbolos, referências, definições e diagnósticos via LSP em vez de
jogar o repositório inteiro no prompt.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.sources.base import ContextSource


class LSPSource(ContextSource):
    """Fonte para inteligência determinística de código via LSP."""

    def __init__(self, lsp_provider: Optional[Any] = None):
        self.lsp_provider = lsp_provider
        self._cached_symbols: Dict[str, ContextItem] = {}

    @property
    def source_name(self) -> str:
        return "lsp"

    def register_symbol_context(
        self,
        symbol_name: str,
        definition_file: str,
        definition_line: int,
        callers: Optional[List[str]] = None,
        references_count: int = 0,
        diagnostics_count: int = 0,
        signature: str = "",
    ) -> ContextItem:
        """Cria e registra item de símbolo de código estruturado."""
        callers_str = "\n".join(f"  - {c}" for c in (callers or [])) or "  - None"
        content = (
            f"PRIMARY SYMBOL: {symbol_name}\n"
            f"SIGNATURE: {signature or symbol_name}\n"
            f"DEFINITION: {definition_file}:{definition_line}\n"
            f"CALLERS:\n{callers_str}\n"
            f"REFERENCES COUNT: {references_count}\n"
            f"DIAGNOSTICS COUNT: {diagnostics_count}"
        )
        item = ContextItem(
            id=f"lsp-symbol-{symbol_name}",
            item_type="code_symbol",
            source_uri=f"lsp://symbols/{symbol_name}",
            content=content,
            title=f"LSP Symbol: {symbol_name}",
            summary=f"Symbol {symbol_name} defined at {definition_file}:{definition_line} ({references_count} refs)",
            abstract=f"{symbol_name} ({definition_file})",
            trust=TrustLevel.TRUSTED_INTERNAL_ARTIFACT,
            authority=AuthorityLevel.ADVISORY,
            relevance=0.9,
            metadata={
                "symbol": symbol_name,
                "file": definition_file,
                "line": definition_line,
                "references": references_count,
            },
        )
        self._cached_symbols[symbol_name] = item
        return item

    def retrieve(
        self,
        query: str = "",
        task_id: str = "",
        budget_hint: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ContextItem]:
        if not query:
            return list(self._cached_symbols.values())

        # Busca por símbolo específico (ou se o nome do símbolo estiver contido na query)
        q_lower = query.lower()
        results = []
        for name, item in self._cached_symbols.items():
            if name.lower() in q_lower or q_lower in name.lower() or q_lower in item.content.lower():
                results.append(item)
        return results
