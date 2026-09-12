"""Harness de medicao do TOPO da recuperacao (backlog HAOS P4 — Etapa 4).

Interpretacao documentada: a spec so diz "Topo da recuperacao" (docs/haos/
RESEARCH_MEDIUM_ABSORPTION.md, Etapa 4). Objetivo detalhado inexistente
in-repo — o contrato minimo derivado da frase e: medir o topo das respostas do
pipeline de recuperacao REAL, de forma reprodutivel, com metrica definida e
resultado reportado. NENHUMA mudanca de algoritmo aqui: o entregavel e a
medicao (a "regua"), nao o otimizador. A escolha entre estrategias (RRF vs
mistura linear) pertence ao P7 e usa este mesmo harness como corpus rotulado.

Corpus: cenarios deterministicos (mesma entrada, mesmo resultado) construidos
contra a maquinaria real — ``RAGFlowStore`` (SQLite FTS5 + breadcrumbs) com
``hybrid_search`` — inspirados nos casos do gold set do P1 (evals/graphrag_lite).
Cada cenario e um rotulo: (consulta, documento esperado no topo). Um cenario de
"miss" (consulta sem match) garante que a regua discrimina.

Metricas (definidas aqui, unicas no repo):
- top-1 hit rate = fracao de consultas cujo documento esperado e o primeiro
  item da lista devolvida por ``hybrid_search``;
- MRR (Mean Reciprocal Rank) = media de 1/rank(esperado) (rank 1-based; 0
  quando ausente da lista).

Determinismo: cada passada de ``run_measurement`` recria os fixtures em
tempdir NOVO e roda a mesma API real; duas passadas precisam devolver o mesmo
report (assert no caso gold set). Nada aqui le texto de codigo-fonte, nada
escreve em ~/.hermes (HERMES_HOME do runner isola cada caso).
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Execucao direta (python evals/graphrag_lite/measure_topo.py): garante a raiz
# do repo no sys.path, como o runner do gold set faz por subprocesso.
if __package__ in (None, ""):
    _repo_root = Path(__file__).resolve().parents[2]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))


@dataclass(frozen=True)
class Scenario:
    """(consulta, docs indexados, documento esperado no topo)."""

    id: str
    query: str
    docs: List[Tuple[str, str]]  # [(doc_path, markdown), ...]
    expected_doc: str


def _doc(path: str, body: str) -> Tuple[str, str]:
    return (path, body)


def build_scenarios() -> List[Scenario]:
    """Cenarios deterministicos de topo contra o RAGFlowStore real.

    Os rotulos foram verificados empiricamente (a passada de medicao roda a
    API real): o documento esperado e o que ``hybrid_search`` coloca em
    primeiro — frase exata, overlap de tokens, termo so no breadcrumb de
    cabecalho, cobertura de varios termos, e o miss sem match no pool.
    """
    return [
        Scenario(
            id="frase-exata",
            query="ProtocolAdapter depends on ModelResolver",
            docs=[
                _doc("doc-a.md", "# Alpha\nProtocolAdapter depends on ModelResolver."),
                _doc("doc-b.md", "# Beta\nProtocolAdapter is mentioned but the phrase differs."),
            ],
            expected_doc="doc-a.md",
        ),
        Scenario(
            id="overlap-parcial",
            query="tokens de autenticacao JWT com refresh",
            docs=[
                _doc("doc-jwt.md",
                     "# Autenticacao\nOs tokens JWT usam refresh e expiram em 15 minutos."),
                _doc("doc-cache.md",
                     "# Cache\nO cache de respostas evita recalculcar o mesmo resultado."),
                _doc("doc-log.md",
                     "# Log\nO log de eventos registra cada requisicao com timestamp."),
            ],
            expected_doc="doc-jwt.md",
        ),
        Scenario(
            id="breadcrumb",
            query="vault de segredos",
            docs=[
                _doc("doc-seg.md",
                     "# Servico de Segredos\n## Vault de Segredos\nOs segredos ficam em cofre local."),
                _doc("doc-rot.md",
                     "# Rotacao\nA rotacao de chaves acontece a cada 90 dias."),
            ],
            expected_doc="doc-seg.md",
        ),
        Scenario(
            id="multi-termo",
            query="metricas de latencia p95 e taxa de erro por servico",
            docs=[
                _doc("doc-met.md",
                     "# Metricas\nColetamos metricas de latencia p95 e taxa de erro por servico "
                     "a cada minuto."),
                _doc("doc-out.md",
                     "# Outlier\nO outlier de latencia e analisado fora do horario."),
            ],
            expected_doc="doc-met.md",
        ),
        Scenario(
            id="miss-sem-pool",
            query="termino que nao existe em nenhum documento do pool",
            docs=[
                _doc("doc-a.md", "# Alpha\nProtocolAdapter depends on ModelResolver."),
            ],
            expected_doc="",
        ),
    ]


def _top1_hit(top_doc: str, expected: str) -> bool:
    return bool(expected) and top_doc == expected


def _mrr(chunks: List[Any], expected: str) -> float:
    if not expected:
        return 0.0
    for idx, chunk in enumerate(chunks, start=1):
        if chunk.doc_path == expected:
            return 1.0 / idx
    return 0.0


def _run_scenario(scenario: Scenario, base: Path) -> Dict[str, Any]:
    from hermes.platform.memory.ragflow_engine import RAGFlowStore

    store = RAGFlowStore(base / "rag.db")
    for doc_path, markdown in scenario.docs:
        store.index_document(doc_path, markdown, doc_id=doc_path)
    chunks = store.hybrid_search(scenario.query, limit=5)
    top_doc = chunks[0].doc_path if chunks else ""
    return {
        "id": scenario.id,
        "top_doc": top_doc,
        "top1_hit": _top1_hit(top_doc, scenario.expected_doc),
        "mrr": _mrr(chunks, scenario.expected_doc),
    }


def run_measurement(scenarios: List[Scenario]) -> Dict[str, Any]:
    """Roda cada cenario contra a maquinaria real em tempdir novo e agrega.

    Retorno determinístico (mesma entrada => mesmo dict): cada cenario com
    top_doc/top1_hit/mrr e o agregado top1_hit_rate/mean_mrr.
    """
    per_scenario: List[Dict[str, Any]] = []
    for scenario in scenarios:
        with tempfile.TemporaryDirectory(prefix="topo-eval-") as td:
            per_scenario.append(_run_scenario(scenario, Path(td)))
    hits = sum(1 for s in per_scenario if s["top1_hit"])
    n = max(len(per_scenario), 1)
    aggregate = {
        "top1_hit_rate": hits / n,
        "mean_mrr": sum(s["mrr"] for s in per_scenario) / n,
    }
    return {"scenarios": per_scenario, "aggregate": aggregate}


# ── A/B de fusão (P7): RRF vs mistura linear — as MESMAS listas ───────────────

def _fused_top(
    rankings: "List[List[Tuple[str, float]]]",
    store: Any,
    *,
    linear: bool,
) -> List[Any]:
    """Aplica o fusor (RRF ou mistura linear) às listas cruas e resolve o topo
    para DocumentChunk (doc_path) com o pool fechado do store real."""
    from hermes.platform.memory.ragflow_engine import (
        LinearScoreFusion,
        ReciprocalRankFusion,
    )

    if linear:
        fused = LinearScoreFusion.fuse(rankings)
    else:
        fused = ReciprocalRankFusion.fuse(rankings, k=60)
    top_ids = [cid for cid, _ in fused[:5]]
    return store.chunks_by_id(top_ids)


def _ab_scenario_metrics(
    scenario: Scenario,
    store: Any,
    rankings: "List[List[Tuple[str, float]]]",
) -> Dict[str, Any]:
    """Metricas (top1 hit, mrr) de UMA consulta para UMA estrategia de fusao."""
    chunks = _fused_top(rankings, store, linear=False)
    top_doc = chunks[0].doc_path if chunks else ""
    rrf = {
        "top1_hit": _top1_hit(top_doc, scenario.expected_doc),
        "mrr": _mrr(chunks, scenario.expected_doc),
    }
    chunks = _fused_top(rankings, store, linear=True)
    top_doc = chunks[0].doc_path if chunks else ""
    linear = {
        "top1_hit": _top1_hit(top_doc, scenario.expected_doc),
        "mrr": _mrr(chunks, scenario.expected_doc),
    }
    return {"rrf": rrf, "linear": linear}


def run_ab_comparison(scenarios: List[Scenario]) -> Dict[str, Any]:
    """A/B reproduzível (P7): RRF contra a mistura linear sobre as MESMAS
    listas ranqueadas do store real, para cada cenario do corpus rotulado.

    Resultado REPORTADO (nunca usado para escolher vencedor no código):
    ``metrics.{rrf,linear}.{top1_hit_rate,mean_mrr}`` + o topo por cenario.
    Determinístico: duas chamadas devolvem o mesmo dict.
    """
    from hermes.platform.memory.ragflow_engine import RAGFlowStore

    tallies = {"rrf": {"hits": 0, "mrr": 0.0}, "linear": {"hits": 0, "mrr": 0.0}}
    per_scenario: List[Dict[str, Any]] = []
    n = max(len(scenarios), 1)

    for scenario in scenarios:
        with tempfile.TemporaryDirectory(prefix="ab-eval-") as td:
            store = RAGFlowStore(Path(td) / "rag.db")
            for doc_path, markdown in scenario.docs:
                store.index_document(doc_path, markdown, doc_id=doc_path)
            fts, lexical = store.rank_lists(scenario.query, limit=5)
            metrics = _ab_scenario_metrics(scenario, store, [fts, lexical])
            for method in ("rrf", "linear"):
                tallies[method]["hits"] += int(metrics[method]["top1_hit"])
                tallies[method]["mrr"] += metrics[method]["mrr"]
            per_scenario.append({
                "id": scenario.id,
                "rrf_top1": metrics["rrf"]["top1_hit"],
                "rrf_mrr": metrics["rrf"]["mrr"],
                "linear_top1": metrics["linear"]["top1_hit"],
                "linear_mrr": metrics["linear"]["mrr"],
            })

    metrics = {
        method: {
            "top1_hit_rate": tallies[method]["hits"] / n,
            "mean_mrr": tallies[method]["mrr"] / n,
        }
        for method in ("rrf", "linear")
    }
    return {
        "scenario_count": len(scenarios),
        "scenarios": per_scenario,
        "metrics": metrics,
    }


def main(argv: "Optional[List[str]]" = None) -> int:
    """Uso direto: python evals/graphrag_lite/measure_topo.py — imprime o
    report do topo atual (RRF, como o store real faz hoje) e sai 0."""
    scenarios = build_scenarios()
    report = run_measurement(scenarios)
    agg = report["aggregate"]
    print(f"=== topo da recuperacao (P4) — {len(scenarios)} cenarios ===")
    for s in report["scenarios"]:
        print(f"  [{s['id']}] top1={'SIM' if s['top1_hit'] else 'nao'} "
              f"mrr={s['mrr']:.3f} top={s['top_doc']!r}")
    print(f"--- top-1 hit rate={agg['top1_hit_rate']:.3f} "
          f"MRR={agg['mean_mrr']:.3f} ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
