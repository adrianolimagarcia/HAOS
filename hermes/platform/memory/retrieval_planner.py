"""Retrieval Planner — intent-aware routing over the HAOS knowledge sources.

Today ``HybridKnowledgeRouter.query()`` walks a FIXED cascade (reconciled →
OKF → RAGFlow → GraphRAG) regardless of what was asked. A "why did we choose
X" question gets the same route as "which file defines Y": word-matching,
which is the wrong instrument for relational and global questions — the
reason RAGFlow ships a retrieval/agentic layer on top of its index.

This module adds the missing *decision*, deterministically and offline:

- ``classify_intent`` — table-driven heuristics (no if/elif ladder, no model):
  GLOBAL (summarize/overview/why-did-we), RELATIONAL (entities/dependencies/
  who-depends-on-whom), CODE (symbols/files/implementations), TEMPORAL
  (when/latest/supersede), FACTUAL (everything else).
- ``plan_retrieval`` — intent → ordered routes, filtered by what is actually
  AVAILABLE (fail-closed: a source without a wired backend is dropped from
  the plan, never simulated, and the drop is recorded in ``skipped`` so the
  caller sees the honest capability picture).
- ``execute_plan`` — runs the routes through injected callables (the router's
  own methods fit the seam), first hit wins, and returns the plan alongside
  the result — every answer says *how* it was found.

It does NOT replace HybridKnowledgeRouter: it decides WHICH source answers
first, then delegates. Wiring it into the router is the documented follow-up
(Sprint C) — the seams here are the router's own callables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

INTENT_GLOBAL = "global"
INTENT_RELATIONAL = "relational"
INTENT_CODE = "code"
INTENT_TEMPORAL = "temporal"
INTENT_FACTUAL = "factual"

_ROUTE_ORDER_BY_INTENT: Dict[str, List[str]] = {
    # overview/why → abstraction first (RAPTOR tree), then graph, then docs
    INTENT_GLOBAL: ["RAPTOR", "GRAPHRAG", "RAGFLOW", "RECONCILED_MEMORY", "OKF"],
    # entities/dependencies → graph first, then tree, then docs
    INTENT_RELATIONAL: ["GRAPHRAG", "RAPTOR", "RAGFLOW", "RECONCILED_MEMORY", "OKF"],
    # symbols/files → lexical/document search first
    INTENT_CODE: ["RAGFLOW", "RECONCILED_MEMORY", "OKF", "RAPTOR", "GRAPHRAG"],
    # when/latest → governed memory first (it tracks supersede), then docs
    INTENT_TEMPORAL: ["RECONCILED_MEMORY", "OKF", "RAGFLOW", "RAPTOR", "GRAPHRAG"],
    INTENT_FACTUAL: ["RECONCILED_MEMORY", "OKF", "RAGFLOW", "RAPTOR", "GRAPHRAG"],
}

_GLOBAL_RE = re.compile(
    r"\b(resumo|geral|vis[ãa]o|overview|summar|por que (escolhemos|adotamos|decidimos)|"
    r"why did we|big picture|tudo sobre|todo o (projeto|sistema)|principalmente)\b", re.I)
_RELATIONAL_RE = re.compile(
    r"\b(quem (usa|depende|chama)|depend\w*ncia|depende de|rela\w+o|relationship|"
    r"conectad[oa] a|impacta|afeta|quem \w+ qual|entre (o|a) \w+ e (o|a))\b", re.I)
_CODE_RE = re.compile(
    r"\b(onde (fica|está|esta)|qual (arquivo|classe|fun\w+o|método|metodo)|"
    r"implementa\w+o|implements|def \w+|class \w+|\.py\b|\.ts\b|\.rs\b|"
    r"arquivo|filepath|código|codigo|source code)\b", re.I)
_TEMPORAL_RE = re.compile(
    r"\b(quando|latest|último|ultimo|mais recente|desde (quando|quando)|"
    r"histórico|historico|supersede|mudou|changed|vers[ãa]o anterior)\b", re.I)


def classify_intent(query: str) -> str:
    """Deterministic intent classification. Most-specific first; FACTUAL is
    the honest default (a wrong specific route degrades retrieval; a wrong
    default is just word-matching, which is what the repo did before)."""
    q = (query or "").strip()
    if not q:
        return INTENT_FACTUAL
    if _GLOBAL_RE.search(q):
        return INTENT_GLOBAL
    if _RELATIONAL_RE.search(q):
        return INTENT_RELATIONAL
    if _CODE_RE.search(q):
        return INTENT_CODE
    if _TEMPORAL_RE.search(q):
        return INTENT_TEMPORAL
    return INTENT_FACTUAL


@dataclass
class RetrievalPlan:
    query: str
    intent: str
    routes: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "intent": self.intent,
            "routes": list(self.routes),
            "skipped": dict(self.skipped),
        }


def plan_retrieval(
    query: str,
    available_sources: Dict[str, bool],
    reason_for_missing: Optional[Dict[str, str]] = None,
) -> RetrievalPlan:
    """Order routes by intent, drop unavailable ones (fail-closed).

    ``available_sources`` maps route name → bool (caller probes: reconciler
    has rows, raptor store has nodes, graphrag client ``available()``, ...).
    A route absent from the map is treated as unavailable — the burden of
    proof is on capability, not on the plan.
    """
    intent = classify_intent(query)
    reason_for_missing = reason_for_missing or {}
    routes, skipped = [], {}
    for route in _ROUTE_ORDER_BY_INTENT[intent]:
        if available_sources.get(route, False):
            routes.append(route)
        else:
            skipped[route] = reason_for_missing.get(route, "not available")
    return RetrievalPlan(query=query, intent=intent, routes=routes, skipped=skipped)


RouteExecutor = Callable[[str], Optional[Dict[str, Any]]]


def execute_plan(
    plan: RetrievalPlan,
    executors: Dict[str, RouteExecutor],
) -> Dict[str, Any]:
    """Run routes in order; first non-None result wins.

    An executor returns a result dict or None (miss). Exceptions in a route
    are recorded and the plan CONTINUES to the next route — one broken source
    must not black out the rest — but the failure is visible in
    ``route_errors`` (never swallowed).
    """
    errors: Dict[str, str] = {}
    for route in plan.routes:
        fn = executors.get(route)
        if fn is None:
            errors[route] = "no executor wired"
            continue
        try:
            result = fn(plan.query)
        except Exception as exc:  # noqa: BLE001 — recorded, plan continues
            errors[route] = f"{type(exc).__name__}: {exc}"
            continue
        if result is not None:
            return {
                "found": True,
                "route": route,
                "intent": plan.intent,
                "result": result,
                "plan": plan.to_dict(),
                "route_errors": errors,
            }
    return {
        "found": False,
        "route": None,
        "intent": plan.intent,
        "result": None,
        "plan": plan.to_dict(),
        "route_errors": errors,
    }
