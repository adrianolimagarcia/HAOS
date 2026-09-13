"""C — UI/Control Plane (Fase 3): ``haos.ui.views`` — seletores puros de view
(AionUI taskboard/approvals + Hermes Studio memory graph) sobre os agregados.

Views = DERIVAÇÃO PURA dos stats (sem I/O): cada view é um subconjunto
nomeado do estado observável, exatamente o que o shell oficial renderizaria a
partir de uma rota de API. Approval decision defaults fail-safe: `True`
quando não há critérios/resultados (nada produzido = nada a revisar; delta 55).
"""

from typing import Any, Dict, List

from hermes.platform.ui.stats import DashboardStats


def taskboard_view(stats: DashboardStats) -> Dict[str, Any]:
    """View AionUI taskboard: contagem por status + tarefas recentes."""
    board = stats.task_board()
    return {
        "view": "taskboard",
        "total": board.total,
        "columns": [
            {"status": status, "count": count}
            for status, count in sorted(board.by_status.items())
        ],
        "recent": board.recent,
    }


def approvals_view(stats: DashboardStats,
                   required_review_stages: int = 1) -> Dict[str, Any]:
    """View AionUI approvals: cards com RESULTADO real que ainda não têm
    veredito de review aprovado (``result.reviewer_verdict == "approved"``)
    ou o mínimo de estágios de aceite.

    Delta 55 (eficiência): sem resultado gravado não há o que revisar — o
    card nunca rodou, então não entra na lista de pendências (resultados de
    execução do engine já nascem ``auto_accept`` em ``complete_task``). Esta
    view só lista pendência quando algo foi de fato produzido e segue sem
    aceite (ex.: resultado registrado por fora sem auto-approve). Fail-safe:
    sem dado, decisão `True`."""
    board = stats.task_board()
    approval_candidates: List[Dict[str, Any]] = []
    for task in board.recent:
        result = task.get("result")
        if result is None:
            continue  # nada produzido => nada a revisar
        result_dict = result.to_dict() if hasattr(result, "to_dict") \
            else (result if isinstance(result, dict) else {})
        verdict = result_dict.get("reviewer_verdict")
        approved = verdict == "approved"
        acceptance = result_dict.get("acceptance") or []
        accepted_stages = sum(
            1 for ac in acceptance if isinstance(ac, dict)
            and ac.get("status") in ("passed", "approved")
        )
        approved = approved and accepted_stages >= required_review_stages
        if not approved:
            spec = task.get("spec") or {}
            run = task.get("run")
            worker = getattr(run, "worker_id", None) if run else None
            goal = spec.get("goal") or spec.get("description") or ""
            approval_candidates.append({
                "id": task.get("id"),
                "title": task.get("title"),
                "status": task.get("status"),
                "goal": goal,
                "worker": worker,
                "posture": task.get("posture"),
                "approved": False,
            })
    return {
        "view": "approvals",
        "pending": approval_candidates,
        "decision": len(approval_candidates) == 0,
    }


def memory_graph_view(stats: DashboardStats) -> Dict[str, Any]:
    """View Hermes Studio memory graph: nós por tipo de evento + arestas por
    trace/correlation (grafos reais derivados, nunca fabricados)."""
    studio = stats.studio()
    nodes = [
        {"type": name, "count": count}
        for name, count in sorted(studio.by_name.items())
    ]
    return {
        "view": "memory_graph",
        "nodes": nodes,
        "trace_edges": len(studio.traces),
        "correlation_edges": len(studio.correlated),
    }


def concurrency_view(stats: DashboardStats) -> Dict[str, Any]:
    """View de controle de concorrência e quotas do ConcurrencyGuard."""
    cg = stats.concurrency()
    return {
        "view": "concurrency",
        "active_global": cg.active_global,
        "max_global": cg.max_global,
        "available_global": cg.available_global,
        "providers": {
            p: {"active": cg.by_provider.get(p, 0), "limit": cg.provider_limits.get(p, 0)}
            for p in sorted(cg.provider_limits.keys())
        },
        "models": {
            m: {"active": cg.by_model.get(m, 0), "limit": cg.model_limits.get(m)}
            for m in sorted(cg.model_limits.keys())
        },
        "active_tasks": cg.active_tasks,
    }


def critical_path_view(stats: DashboardStats) -> Dict[str, Any]:
    """View do Caminho Crítico (CPM) e Prioridade Herdada (PIP)."""
    cp = stats.critical_path()
    return {
        "view": "critical_path",
        "critical_path_ids": cp.critical_path_ids,
        "inherited_priorities": cp.inherited_priorities,
        "total_tasks_evaluated": cp.total_tasks_evaluated,
    }
