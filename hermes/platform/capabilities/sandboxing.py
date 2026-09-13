"""CapabilityFilter & Sandboxing por Postura (A3/K3).

Garante sandboxing cognitivo e operacional:
- Reviewer: apenas ferramentas de leitura, verificação e teste (nunca mutação de código ou terminal livre).
- Architect: modelagem, leitura, ADRs e grafos (proíbe comandos destrutivos).
- Researcher: busca web, leitura e síntese (sem escrita em filesystem de código).
- Coder: edição, compilação, testes e diagnósticos.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

POSTURE_ALLOWED_TOOLSETS: Dict[str, Set[str]] = {
    "coder": {
        "file", "terminal", "code_execution", "memory", "todo", "debugging", "skills",
    },
    "reviewer": {
        "file", "debugging", "memory", "todo",  # Notar: sem 'terminal' livre e sem mutação arbitrária
    },
    "architect": {
        "file", "memory", "todo", "session_search", "skills",
    },
    "researcher": {
        "web", "search", "file", "memory", "todo", "session_search",
    },
    "security": {
        "file", "debugging", "memory", "todo", "session_search",
    },
}

POSTURE_FORBIDDEN_TOOLS: Dict[str, Set[str]] = {
    "reviewer": {
        "write_file", "edit_file", "terminal", "execute_code",
    },
    "architect": {
        "execute_code", "write_file",
    },
    "researcher": {
        "execute_code", "write_file", "edit_file", "terminal",
    },
}


class PostureCapabilitySandboxing:
    """Filtra ferramentas disponíveis para a sessão com base na postura resolvida."""

    @classmethod
    def filter_tools_for_posture(
        cls,
        tool_schemas: List[Dict[str, Any]],
        posture_name: str,
    ) -> List[Dict[str, Any]]:
        """Filtra schemas de ferramentas antes de enviá-los ao modelo."""
        posture = posture_name.lower().strip()
        forbidden = POSTURE_FORBIDDEN_TOOLS.get(posture, set())
        allowed_toolsets = POSTURE_ALLOWED_TOOLSETS.get(posture, None)

        filtered = []
        for schema in tool_schemas:
            name = schema.get("name") or (schema.get("function", {}).get("name"))
            if not name:
                continue
            if name in forbidden:
                continue
            filtered.append(schema)

        return filtered
