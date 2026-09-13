"""Context Engine — otimização composta (A3, Emenda 30).

``optimize_package`` = seleção por relevância/orçamento + compressão que
preserva decisões, retornando um NOVO pacote com digest/token_count
recomputados. Puro: o pacote de entrada nunca é mutado.
"""

import hashlib
import json
from dataclasses import replace
from typing import Dict, List, Optional

from hermes.platform.memory.context_engine.builder import (
    ContextPackage, SectionContent,
)
from hermes.platform.memory.context_engine.selection import fit_budget
from hermes.platform.memory.context_engine.compression import compress_package


def _digest(sections: Dict[str, SectionContent]) -> str:
    content_str = json.dumps({k: v.content for k, v in sections.items()},
                             default=str)
    return hashlib.sha256(content_str.encode()).hexdigest()


def _tokens(sections: Dict[str, SectionContent]) -> int:
    content_str = json.dumps({k: v.content for k, v in sections.items()},
                             default=str)
    return len(content_str) // 4


def optimize_package(
    package: ContextPackage,
    *,
    task_signals: Optional[List[str]] = None,
    decision_markers: Optional[List[str]] = None,
    budget_tokens: Optional[int] = None,
) -> ContextPackage:
    """Nova instância otimizada (seleção + compressão), mesmos id/limite."""
    kept, _dropped = fit_budget(package, budget_tokens=budget_tokens,
                                task_signals=task_signals)
    selected = replace(
        package,
        sections={k: package.sections[k] for k in kept},
    )
    compressed = compress_package(selected, markers=decision_markers)
    return replace(
        compressed,
        token_count=_tokens(compressed.sections),
        digest_hash=_digest(compressed.sections),
    )
