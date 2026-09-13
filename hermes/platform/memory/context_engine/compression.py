"""Context Engine — compressão que preserva decisões (A3, Emenda 30).

``compress_text`` nunca apaga linhas que carregam decisão (marcadores de
decisão/changelog/rationale): essas linhas são preservadas VERBATIM na ordem
original; o resto é compactado com uma nota contável. Heurística determinística
e documentada — o runtime não usa LLM para comprimir (decisão de política).
"""

import re
from typing import List, Optional

DEFAULT_DECISION_MARKERS = (
    "DECIDED", "DECISION", "ACCEPTED", "REJECTED", "APPROVED", "REJECT",
    "CHANGED", "FIXED", "CHOSEN", "STATUS:", "VERSION", "RESOLUTION",
    "CONCLUSION", "RATIONALE", "BECAUSE", "OUTCOME",
)

_HEAD_KEEP = 3  # primeiras linhas (cabeçalho/contexto) sempre preservadas


def is_decision_line(line: str, markers: List[str]) -> bool:
    upper = line.upper()
    return any(m.upper() in upper for m in markers)


def split_decision_lines(
    text: str, markers: Optional[List[str]] = None
) -> List[str]:
    """Linhas que carregam decisão, na ordem original."""
    markers = list(markers) if markers is not None else list(DEFAULT_DECISION_MARKERS)
    return [ln for ln in text.splitlines() if is_decision_line(ln, markers)]


def compress_text(
    text: str,
    *,
    markers: Optional[List[str]] = None,
    max_chars: Optional[int] = None,
    ellipsis: str = "…",
) -> str:
    """Comprime preservando decisões: linhas-decisão verbatim + cabeçalho;
    demais viram nota de compactação. Idempotente quando não há o que cortar
    (texto curto ou sem enchimento)."""
    markers = list(markers) if markers is not None else list(DEFAULT_DECISION_MARKERS)
    if max_chars is not None and len(text) <= max_chars:
        return text
    lines = text.splitlines()
    decision = [ln for ln in lines if is_decision_line(ln, markers)]
    if not decision:
        # Sem decisões, mantém cabeçalho e encurta com elipse no corpo.
        kept = lines[:_HEAD_KEEP]
        if len(lines) > _HEAD_KEEP:
            kept.append(f"{ellipsis} [+{len(lines) - _HEAD_KEEP} lines compacted]")
        return "\n".join(kept)
    dropped_filler = len(lines) - len(decision) - _HEAD_KEEP
    out = list(decision)
    note = f"{ellipsis} [+{max(0, dropped_filler)} lines compacted; decisions preserved]"
    if dropped_filler > 0:
        out.append(note)
    result = "\n".join(out)
    if max_chars is not None and len(result) > max_chars:
        return result[:max_chars].rsplit("\n", 1)[0] + f"\n{note}"
    return result


def compress_package(
    package,
    *,
    markers: Optional[List[str]] = None,
    max_chars_per_section: Optional[int] = None,
):
    """Nova instância (mesmo id/limite) com cada seção comprimida; entrada
    intacta. Só seções cujo conteúdo seja ``str`` são comprimidas (dicts JSON
    são estrutura, não narrativa — permanecem)."""
    from dataclasses import replace
    sections = {}
    for key, section in package.sections.items():
        if isinstance(section.content, str) and (
            max_chars_per_section is None
            or len(section.content) > max_chars_per_section
        ):
            content = compress_text(
                section.content,
                markers=markers,
                max_chars=max_chars_per_section,
            )
            sections[key] = replace(section, content=content)
        else:
            sections[key] = section
    return replace(package, sections=sections)
