"""Worker de pesquisa profunda (Fase 1, port puro do padrão DeerFlow 1.x).

Cadeia: decompose (pergunta -> sub-perguntas tipadas, ≈ Plan/Step) -> por
sub-pergunta de pesquisa, ``fetcher`` injetado devolve resultados canônicos
{url,title,content,score,...} (a emenda pura do DeerFlow: extractor/collector
consomem ESSA forma, sem engine) -> Finding com citações -> ``aggregate``
sintetiza resposta única + lista de citações deduplicada.

FAIL-CLOSED: sem ``fetcher`` injetado, ``probe`` reporta indisponível e
``acquire`` lança `ResearchProviderError` ANTES de qualquer trabalho — nunca
rede em processo por padrão. O fetcher é um callable seam: a implementação de
produção é out-of-process (adaptador de subprocesso/serviço, forma B do
INTEGRATIONS) e pluga aqui sem tocar no núcleo.
"""

import inspect
import time
from typing import Any, Callable, Dict, List, Optional

from hermes.platform.capabilities.registry import (
    Capability, CapabilityProvider, CapabilityRegistry,
)
from hermes.platform.capabilities.research.aggregate import aggregate
from hermes.platform.capabilities.research.models import (
    Citation, Finding, ResearchArtifact, ResearchQuestion,
)

_CANONICAL_KEYS = ("url", "title", "content", "score")


class ResearchProviderError(RuntimeError):
    pass


def decompose(
    question: str,
    *,
    max_sub_questions: int = 3,
    depth: int = 1,
) -> List[ResearchQuestion]:
    """Decomposição pura e determinística (total).

    - question vazia/whitespace -> [] (sem erro).
    - max_sub_questions < 1 -> [].
    - caso contrário, 1 <= len <= max_sub_questions, depth <= ``depth``.

    O DEFAULT não inventa sub-tópicos (nunca excede o limite e não alucina):
    devolve a própria pergunta raiz como o único step de pesquisa. Uma
    ``decompose_fn`` injetada (LLM/serviço) pode produzir a árvore real —
    o contrato de limites acima vale para ela também.
    """
    if max_sub_questions < 1 or not (question or "").strip():
        return []
    root = ResearchQuestion(
        question=question.strip(),
        rationale="",
        need_search=True,
        step_type="research",
        depth=0,
    )
    if depth < 1:
        return []
    return [root]


def _result_to_citation(result: Dict[str, Any], query: str) -> Citation:
    """Adapta o result dict canônico (seam DeerFlow) para Citation."""
    content = result.get("content") or result.get("description") or ""
    return Citation(
        url=result.get("url", ""),
        title=result.get("title") or "Untitled",
        snippet=(content or "")[:500],
        accessed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        relevance_score=float(result.get("score") or 0.0),
        extra={"query": query, "result_type": result.get("result_type", "page")},
    )


def _claim_from_results(results: List[Dict[str, Any]], limit: int = 2000) -> str:
    parts = []
    for r in results:
        title = r.get("title") or "Untitled"
        content = (r.get("content") or r.get("description") or "")[:300]
        parts.append(f"{title}: {content}" if content else title)
    claim = "\n".join(parts)
    return claim[:limit]


class ResearchProvider(CapabilityProvider):
    """Provider agentic da capability ``deep-research`` (≈ DeerFlow researcher).

    ``fetcher(question: str) -> List[Dict]`` devolve os resultados canônicos
    {url,title,content,score,...}; pode ser síncrono OU retornar awaitable
    (acquire espera). ``decompose_fn`` opcional substitui o decompose default.
    """

    provider_id = "research-worker"

    def __init__(
        self,
        fetcher: Optional[Callable[[str], Any]] = None,
        *,
        decompose_fn: Optional[Callable[..., List[ResearchQuestion]]] = None,
        max_sub_questions: int = 3,
        max_depth: int = 1,
    ):
        self.fetcher = fetcher
        self.decompose_fn = decompose_fn
        self.max_sub_questions = max_sub_questions
        self.max_depth = max_depth

    # -- CapabilityProvider -------------------------------------------------
    async def probe(self) -> Dict[str, Any]:
        """Sem efeito colateral: NÃO invoca o fetcher."""
        return {
            "provider_id": self.provider_id,
            "available": self.fetcher is not None,
            "features": ["decompose", "web-research", "synthesis"],
        }

    async def acquire(self, request: Dict[str, Any]) -> ResearchArtifact:
        """Roda decompose + fetch + aggregate; retorna o artefato de evidência.

        ``request``: {question, max_sub_questions?, depth?}. Sem fetcher ->
        `ResearchProviderError` (fail-closed). Pergunta vazia/ausente ->
        artefato com status="failed" e erro em evidence (nunca raise; o
        artefato é sempre JSON round-trippable).
        """
        if self.fetcher is None:
            raise ResearchProviderError(
                "research-worker: no fetcher injected (fail-closed). "
                "Production fetchers run out-of-process (INTEGRATIONS B); "
                "the pure seam requires an explicit callable."
            )
        question = (request.get("question") or "").strip()
        artifact = ResearchArtifact(
            artifact_id=request.get("artifact_id") or f"art-res-{int(time.time())}",
            question=question,
            status="running",
            produced_by=self.provider_id,
        )
        if not question:
            artifact.status = "failed"
            artifact.evidence = {"error": "empty question"}
            return artifact

        max_q = int(request.get("max_sub_questions", self.max_sub_questions))
        depth = int(request.get("depth", self.max_depth))
        if self.decompose_fn is not None:
            questions = self.decompose_fn(
                question, max_sub_questions=max_q, depth=depth
            )
        else:
            questions = decompose(question, max_sub_questions=max_q, depth=depth)

        findings: List[Finding] = []
        for idx, q in enumerate(questions):
            if not q.need_search or q.step_type not in ("research",):
                continue
            raw = self.fetcher(q.question)
            if inspect.isawaitable(raw):
                raw = await raw
            results = raw or []
            results = [r for r in results if isinstance(r, dict)]
            citations = [_result_to_citation(r, q.question) for r in results]
            findings.append(Finding(
                question=q.question,
                claim=_claim_from_results(results),
                citations=citations,
                confidence=_avg_relevance(citations),
                produced_by=self.provider_id,
                step_index=idx,
            ))
        artifact.findings = findings
        return aggregate(artifact)


    async def release(self, handle: Any) -> None:
        """Sem estado para teardown; existe por simetria do contrato ABC."""
        return None


def _avg_relevance(citations: List[Citation]) -> float:
    if not citations:
        return 0.0
    return round(
        sum(c.relevance_score for c in citations) / len(citations), 4
    )


def register_research_provider(
    registry: CapabilityRegistry,
    provider: Optional[CapabilityProvider] = None,
) -> CapabilityProvider:
    """Registra a capability ``deep-research`` (agentic) + provider vivo.

    Fica FORA dos defaults do CapabilityRegistry (acoplamento zero — mesma
    decoupling do VisionWorker); chame ao montar o runtime HAOS.
    """
    provider = provider or ResearchProvider()
    registry.register(Capability(
        id="deep-research",
        execution_kind="agentic",
        providers=[provider.provider_id],
        features=["decompose", "web-research", "synthesis"],
    ))
    registry.register_provider(provider)
    return provider
