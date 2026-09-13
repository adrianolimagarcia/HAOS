"""Agregação pura de pesquisa (Fase 1, DeerFlow) — resposta + citações.

Port da lógica pura de merge do DeerFlow 1.x (``extractor.merge_citations``,
relevance-higher-wins) + síntese do reporter: findings -> UMA resposta, com a
lista de citações deduplicada por url carregada ao lado. Não faz I/O e não
muta o artefato de entrada.
"""

from typing import Callable, Dict, List, Optional

from hermes.platform.capabilities.research.models import (
    Citation, Finding, ResearchArtifact,
)

_DEFAULT_SYNTHESIZER: Callable[[str, List[Finding]], str] = lambda q, findings: (
    "\n".join(f"[{i + 1}] {f.claim}" for i, f in enumerate(findings))
)


def _merge_citations(existing: List[Citation], new: List[Citation]) -> List[Citation]:
    """Dedupe por url; em conflito fica a maior relevance_score; título e
    snippet melhores (mais longos) vencem (≈ DeerFlow merge_citations)."""
    by_url: Dict[str, Citation] = {}

    def _norm(title: str) -> str:
        return "" if title in ("", "Untitled") else title

    for cit in existing + new:
        prior = by_url.get(cit.url)
        if prior is None:
            by_url[cit.url] = cit
            continue
        title_a, title_b = _norm(prior.title), _norm(cit.title)
        best_title = (title_a if len(title_a) >= len(title_b) else title_b) or "Untitled"
        best_snippet = (
            prior.snippet if len(prior.snippet) >= len(cit.snippet) else cit.snippet
        )
        by_url[cit.url] = Citation(
            url=cit.url,
            title=best_title,
            snippet=best_snippet,
            domain=prior.domain or cit.domain,
            accessed_at=prior.accessed_at or cit.accessed_at,
            relevance_score=max(prior.relevance_score, cit.relevance_score),
            extra={**prior.extra, **cit.extra},
        )
    return list(by_url.values())


def _mean_confidence(findings: List[Finding]) -> float:
    if not findings:
        return 0.0
    return round(sum(f.confidence for f in findings) / len(findings), 4)


def aggregate(
    artifact: ResearchArtifact,
    *,
    synthesize: Optional[Callable[[str, List[Finding]], str]] = None,
) -> ResearchArtifact:
    """Sintetiza os findings em ``answer`` e agrega citações por url.

    Pura: retorna um NOVO artefato; o de entrada não muda. ``evidence`` do
    resultado é o slot JSON que encaixa em proposal["evidence"] do Ouroboros.
    ``status="complete"`` somente quando há findings; sem findings o artefato
    permanece com o status original (draft/failed) e answer None.
    """
    merged = _merge_citations(artifact.citations, [
        c for f in artifact.findings for c in f.citations
    ])
    result = ResearchArtifact(
        artifact_id=artifact.artifact_id,
        question=artifact.question,
        findings=[f for f in artifact.findings],
        citations=merged,
        confidence=_mean_confidence(artifact.findings),
        model=artifact.model,
        status="complete" if artifact.findings else artifact.status,
        uncertainty=artifact.uncertainty,
        produced_by=artifact.produced_by,
    )
    if artifact.findings:
        fn = synthesize or _DEFAULT_SYNTHESIZER
        result.answer = fn(artifact.question, result.findings)
        result.evidence = {
            "findings": [
                {
                    "question": f.question,
                    "claim": f.claim,
                    "citations": [c.url for c in f.citations],
                    "confidence": f.confidence,
                    "step_index": f.step_index,
                }
                for f in result.findings
            ],
            "citations": [c.to_dict() for c in merged],
            "confidence": result.confidence,
        }
    return result
