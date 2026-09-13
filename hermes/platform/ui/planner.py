"""C — UI/Control Plane (Fase 3): ``haos.ui.planner`` — bridge de planejamento
para sessões ACP a partir do estado observável do dashboard (delta 49).

O que faz: deriva um *brief* textual do MESMO estado que o control plane
renderiza (``dashboard_payload`` + evolution pending) — nunca segunda fonte,
nada fabricado: sem store o brief diz explicitamente que não há estado. Esse
brief é o texto que uma sessão ACP de planejamento recebe via
``session/prompt`` (wire JSON-RPC/stdio do K5, ``protocols/acp/adapter.py`` —
este módulo NÃO reimplementa o transport; apenas deriva o prompt e orquestra a
sessão com o cliente canônico).

Fail-closed: ``planning_brief`` é pura (sem I/O); a execução da sessão recebe o
``server_command`` (argv) do agente ACP e o ``cwd`` — sem comando o chamador
não chama (a rota do plugin responde 409 antes de qualquer spawn).

Imports de topo restritos a stdlib (regra do platform); o adapter ACP é
stdlib-only e importado lazy dentro da função de sessão para manter o import
deste módulo leve.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def planning_brief(stats: Any, *, instruction: Optional[str] = None) -> str:
    """Brief textual do estado observável, derivado 100% das views reais.

    Renderiza taskboard (total + contagens por status), approvals pendentes,
    nós do memory graph e propostas de evolution ainda sem decisão — os MESMOS
    números do payload que o dashboard mostra. Sem store o brief é explícito
    (``available() False``): nunca inventa dados para parecer produtivo.
    ``instruction`` (opcional) é a intenção humana anexada ao estado.
    """
    from hermes.platform.ui.dashboard import dashboard_payload  # noqa: PLC0415

    evolution_pending: List[Dict[str, Any]] = []
    if stats is None:
        payload: Dict[str, Any] = {
            "taskboard": {"view": "taskboard", "total": 0, "columns": [],
                          "recent": []},
            "approvals": {"view": "approvals", "pending": [], "decision": False},
            "memory_graph": {"view": "memory_graph", "nodes": []},
        }
        available = False
    else:
        available = bool(stats.available())
        payload = dashboard_payload(stats)
        try:
            evolution_pending = stats.evolution_pending()
        except Exception:  # pragma: no cover - fail-closed defensivo
            evolution_pending = []

    taskboard = payload.get("taskboard") or {}
    approvals = payload.get("approvals") or {}
    memory_nodes = (payload.get("memory_graph") or {}).get("nodes", [])

    lines: List[str] = []
    lines.append("HAOS control-plane state (derived from canonical stores).")
    if not available:
        lines.append("NOTE: no kanban store configured — taskboard is empty.")
    if instruction:
        lines.append(f"Operator instruction: {instruction}")
    lines.append("")
    lines.append("Taskboard:")
    lines.append(f"  total: {taskboard.get('total', 0)}")
    for col in taskboard.get("columns", []):
        lines.append(f"  {col.get('status', '?')}: {col.get('count', 0)}")
    lines.append("Approvals pending (cards awaiting review): "
                 f"{len(approvals.get('pending') or [])}")
    lines.append("Memory graph node kinds: "
                 f"{', '.join(str(n.get('type')) for n in memory_nodes) if memory_nodes else 'none'}")
    lines.append("Evolution proposals awaiting decision: "
                 f"{len(evolution_pending)}")
    for proposal in evolution_pending:
        lines.append(f"  - {proposal.get('proposal_id', '?')}")
    return "\n".join(lines)


def run_planning_session(
    server_command: List[str],
    cwd: str,
    prompt: str,
    *,
    request_timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Roda UMA sessão ACP de planejamento com o brief dado.

    Usa o ``ACPSessionClient`` canônico do K5 (spawn + initialize + session/new
    + session/prompt + close) — nunca duplica o transport. Retorna o resultado
    cru capturado + identidade do agente + session_id. Erros do peer propagam
    (``ACPUnavailableError``/``ACPError``) — o chamador decide o fail-closed.
    """
    from hermes.platform.protocols.acp.adapter import (  # noqa: PLC0415
        ACPSessionClient,
    )

    kwargs: Dict[str, Any] = {}
    if request_timeout_s is not None:
        kwargs["request_timeout_s"] = request_timeout_s
    with ACPSessionClient(list(server_command), **kwargs) as client:
        session = client.new_session(cwd)
        raw = client.send_text(session, prompt)
        identity = client.identity
        return {
            "session_id": session.session_id,
            "agent": {
                "name": getattr(identity, "name", None),
                "version": getattr(identity, "version", None),
            },
            "result": raw,
        }
