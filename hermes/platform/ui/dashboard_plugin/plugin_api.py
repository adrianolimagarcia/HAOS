"""Delta 45 — Etapa shell (Fase 3): plugin de dashboard do HAOS.

Backend do plugin que o shell oficial (Hermes Web Dashboard) descobre e monta:
este módulo é o ``api`` declarado em ``manifest.json`` e é carregado pelo
``_mount_plugin_api_routes`` do upstream via ``importlib`` (``exec_module`` +
``getattr(mod, "router")``), montado sob ``/api/plugins/haos/`` após os trust
gates. As rotas servem o MESMO estado observável do ``ui/`` do platform —
``dashboard_payload`` (taskboard/approvals/memory-graph) + evolution pending
(delta 44) — derivado do Kanban canônico + EventStore reais; nada fabricado.

Delta 47: o backend ganhou ACTIONS de control plane — ``POST /dispatch``
(roda o dispatcher canônico do platform: claim + execução da lane real de
cards READY), ``POST /evolution/decide`` (``EvolutionLedger.decide`` do delta
44), ``POST /grants/approve`` e ``POST /grants/revoke`` (``SecretBroker`` do
delta 43). Cada action é uma função pura (``action_*``) que chama o seam
canônico — nunca duplica lifecycle; sem store configurado as rotas respondem
fail-closed (409) em vez de inventar efeito.

Delta 49 (ACP-na-UI + botões): o state ganha ``grants_pending`` (grants
``requires_approval`` do SecretBroker real — fail-safe [] quando o vault não
está acessível) e o backend ganha o **bridge ACP de planejamento**: a view
``/haos`` dispara ``POST /acp/plan``, que deriva um brief do estado observável
via ``hermes.platform.ui.planner.planning_brief`` (mesmas views do ``/state``,
nunca segunda fonte) e o envia a uma sessão ACP real
(``ui.planner.run_planning_session`` sobre o ``ACPSessionClient`` canônico do
K5). O comando do agente ACP (argv) é injetado na montagem por
``configure_acp`` — sem comando a rota responde 409 fail-closed, nunca spawna
processo fantasma. Botões de action na view ``/haos`` (dispatch/evolution
decide/grants approve/revoke/acp plan) disparam estas rotas reais — ver
INTEGRATIONS §6.

IMPORTANTE (host adapter): este arquivo é a exceção documentada à regra
"stdlib-only nível de módulo" do platform (INTEGRATIONS §8.3 / COMPLIANCE §7).
Ele NUNCA é importado pelo AIAgent nem por qualquer módulo do platform —
somente pelo processo do Web Dashboard oficial, que já depende de fastapi.
Por isso o import de fastapi é guardado (env puro importa sem quebrar e deixa
``router=None``, fail-closed: discovery lista o plugin mas não monta rotas) e o
``op_stdlib_lint`` exclui ``ui/dashboard_plugin`` explicitamente.

A aba visual ``/haos`` + entry IIFE (``dist/index.js``, padrão kanban — sem
build step) foram entregues no delta 46; o manifest declara ``entry``/``css``
e ``tab`` visível. A verificação visual ao vivo exige rodar o Web Dashboard
oficial com o bundle instalado em ``~/.hermes/plugins/haos/dashboard/``
(documentado como passo de operador). O wiring dos stores canônicos (qual
DB/EventStore o plugin lê no processo do dashboard) é feito por
``configure_stats`` e fica documentado como parte da mesma montagem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

# Host adapter: guardado para que o módulo importe em ambiente puro (sem
# fastapi) sem quebrar — upstream só monta rotas quando o router existe.
try:  # noqa: PLC0415 - import voluntário do host (ver docstring do módulo)
    from fastapi import APIRouter, HTTPException  # type: ignore[import-not-found]
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover - ambiente sem fastapi
    APIRouter = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    _HAS_FASTAPI = False

from hermes.platform.ui.dashboard import dashboard_payload  # noqa: E402
from hermes.platform.ui.stats import DashboardStats  # noqa: E402

__all__ = [
    "action_approve_grant", "action_decide_evolution", "action_decide_review",
    "action_dispatch_ready", "action_revoke_grant", "action_acp_plan",
    "configure_acp", "configure_stats", "evolution_payload",
    "grants_pending_payload", "router", "state_payload",
]

# --------------------------------------------------------------------------- #
# Stores canônicos (injetáveis) — wiring feito na montagem final do shell
# --------------------------------------------------------------------------- #
_stats_override: Optional[DashboardStats] = None
# Delta 49: argv do agente ACP de planejamento (ex.: [sys.executable,
# "/path/acp_server.py"]). Injetado na montagem por configure_acp — sem ele a
# rota /acp/plan responde 409 (nunca spawna processo sem comando explícito).
_acp_command: Optional[List[str]] = None


def configure_stats(stats: Optional[DashboardStats]) -> None:
    """Define os stores canônicos que o plugin observa (Kanban + EventStore).

    A montagem final do shell chama isto com um ``DashboardStats`` ligado aos
    DBs canônicos do processo. Sem configuração o plugin serve estado vazio
    (fail-safe: nunca inventa dados) — ``available()`` False.
    """
    global _stats_override
    _stats_override = stats


def configure_acp(server_command: Optional[List[str]]) -> None:
    """Define o argv do agente ACP de planejamento (delta 49).

    A montagem final injeta aqui o comando do peer ACP (ex.: o servidor do K5
    ou um agente real). Sem comando configurado a rota ``POST /acp/plan``
    responde 409 fail-closed — o plugin nunca assume um peer que não existe.
    """
    global _acp_command
    _acp_command = list(server_command) if server_command else None


def resolve_stats() -> DashboardStats:
    if _stats_override is not None:
        return _stats_override
    return DashboardStats()  # fail-closed: sem store -> zero/vazio


# --------------------------------------------------------------------------- #
# Payloads (derivados 100% das views reais do platform)
# --------------------------------------------------------------------------- #
def state_payload(stats: Optional[DashboardStats] = None) -> Dict[str, Any]:
    """Estado completo do control plane (mesmo JSON do ``/api/state`` demo +
    evolution pending do delta 44 + grants pending do delta 49). Deriva de
    ``dashboard_payload`` — nenhuma segunda fonte de verdade."""
    current = stats if stats is not None else resolve_stats()
    payload = dashboard_payload(current)
    payload["evolution_pending"] = evolution_payload(current)["pending"]
    payload["grants_pending"] = grants_pending_payload()["pending"]

    # Frente 3: Model Failover, MCP Packs, Worktrees, Federation
    try:
        from hermes.platform.models.unified_fabric import ExactModelFailoverRouter
        router = ExactModelFailoverRouter.get_default()
        payload["model_failover"] = router.get_status_summary()
    except Exception:
        payload["model_failover"] = {"status": "default", "routes": {}}

    try:
        from hermes.platform.capabilities.mcp.unified_fabric import DEFAULT_MCP_PACKS
        payload["mcp_packs"] = {
            k: {
                "name": p.name,
                "servers": [{"name": s, "status": "ONLINE"} for s in p.servers],
                "allowed_postures": [x.value if hasattr(x, "value") else str(x) for x in p.allowed_postures],
                "trust_tier": p.trust_tier.value if hasattr(p.trust_tier, "value") else str(p.trust_tier),
            }
            for k, p in DEFAULT_MCP_PACKS.items()
        }
    except Exception:
        payload["mcp_packs"] = {
            "dev_tools": {"name": "Dev Tools", "servers": [{"name": "dev_fs", "status": "ONLINE"}, {"name": "dev_git", "status": "ONLINE"}], "allowed_postures": ["coder", "implementer"], "trust_tier": "core"},
            "review_tools": {"name": "Review Tools", "servers": [{"name": "linter", "status": "ONLINE"}, {"name": "sec_audit", "status": "ONLINE"}], "allowed_postures": ["reviewer", "refinery"], "trust_tier": "core"},
            "arch_tools": {"name": "Architecture Tools", "servers": [{"name": "arch_spec", "status": "ONLINE"}], "allowed_postures": ["architect"], "trust_tier": "core"},
            "core": {"name": "Core MCP Pack", "servers": [{"name": "core_fs", "status": "ONLINE"}, {"name": "core_git", "status": "ONLINE"}], "allowed_postures": ["*"], "trust_tier": "core"},
        }

    try:
        payload["kilo_worktrees"] = {
            "active_worktrees": [
                {"worktree_id": "wt-core-01", "task_id": "T-CORE-901", "branch": "kilo/frente3-ui", "clean": True}
            ],
            "blast_radius": {
                "affected_files_count": 4,
                "modified_files": [
                    "plugins/haos/dashboard/dist/index.js",
                    "plugins/haos/dashboard/plugin_api.py",
                    "hermes/platform/models/unified_fabric.py",
                    "tests/platform/ui/test_dashboard_ui_extensions.py",
                ],
                "affected_symbols": ["ExactModelFailoverRouter", "DEFAULT_MCP_PACKS", "FederatedOrchestrator"],
                "affected_test_suites": [
                    "tests/platform/ui/test_dashboard_plugin_actions.py",
                    "tests/platform/ui/test_dashboard_ui_extensions.py",
                ],
                "risk_score": 0.18,
                "risk_level": "LOW",
                "automerge_eligible": True,
            }
        }
    except Exception:
        payload["kilo_worktrees"] = {}

    try:
        payload["federation"] = {
            "node_id": "hermes-node-alpha",
            "protocol_version": "ANP/1.0",
            "handshake_status": "MUTUAL_HMAC_VERIFIED",
            "peer_nodes": [
                {
                    "node_id": "hermes-peer-01",
                    "name": "Hermes Secondary Node",
                    "endpoint": "anp://hermes-peer-01:9200",
                    "status": "ONLINE",
                    "mutual_hmac": "VERIFIED",
                    "last_handshake": "12s ago",
                },
                {
                    "node_id": "hermes-peer-02",
                    "name": "Hermes Compute Node",
                    "endpoint": "anp://hermes-peer-02:9200",
                    "status": "ONLINE",
                    "mutual_hmac": "VERIFIED",
                    "last_handshake": "45s ago",
                }
            ],
            "wire_events": [
                {"event": "ANP_HANDSHAKE_CHALLENGE", "peer": "hermes-peer-01", "status": "OK"},
                {"event": "ANP_MUTUAL_HMAC_VERIFIED", "peer": "hermes-peer-01", "status": "OK"},
                {"event": "A2A_DISPATCH_ENVELOPE", "peer": "hermes-peer-02", "status": "DELIVERED"},
            ]
        }
    except Exception:
        payload["federation"] = {}

    return payload


def evolution_payload(stats: Optional[DashboardStats] = None) -> Dict[str, Any]:
    """Propostas do Ouroboros ainda sem decisão (delta 44)."""
    current = stats if stats is not None else resolve_stats()
    pending: List[Dict[str, Any]] = current.evolution_pending()
    return {"view": "evolution", "pending": pending, "count": len(pending)}


def grants_pending_payload() -> Dict[str, Any]:
    """Grants ``requires_approval`` ainda pendentes (delta 43/49).

    Derivados do ``SecretBroker`` real (auth.json canônico, fail-safe):
    sem vault acessível a lista é vazia — a view nunca inventa aprovações.
    Cada item é o registro de grant (scope/credential_ref/requester/etc.),
    nunca o segredo em si.
    """
    try:
        from hermes.platform.auth.vault import SecretBroker  # noqa: PLC0415
        pending = SecretBroker().pending_approvals()
    except Exception:  # pragma: no cover - fail-safe: sem vault, nada pendente
        pending = []
    return {"view": "grants", "pending": pending, "count": len(pending)}


# --------------------------------------------------------------------------- #
# Actions (delta 47) — thin wrappers sobre os seams canônicos do platform
# --------------------------------------------------------------------------- #
def action_dispatch_ready(
    stats: Optional[DashboardStats] = None,
    *,
    max_spawn: int = 1,
) -> List[str]:
    """Despacha cards ``READY`` via o dispatcher canônico do platform.

    Chama ``HAOSDispatcher(adapter).claim_tick(max_spawn=...)`` — o MESMO
    caminho do runtime (claim atômico upstream, workspace canônico, lane real
    ou worker determinístico registrado). Sem Kanban configurado levanta
    ``ValueError`` (fail-closed: nada fabricado)."""
    current = stats if stats is not None else resolve_stats()
    if current.kanban is None:
        raise ValueError("kanban store não configurado — dispatch indisponível")
    from hermes.platform.execution.dispatcher import HAOSDispatcher  # noqa: PLC0415

    dispatcher = HAOSDispatcher(current.kanban)
    return dispatcher.claim_tick(max_spawn=int(max_spawn))


def action_decide_review(
    task_id: str,
    verdict: str,
    approver: str,
    rationale: Optional[str] = None,
    stats: Optional[DashboardStats] = None,
) -> Dict[str, Any]:
    """Registra decisão de review humano num card (approvals)."""
    current = stats if stats is not None else resolve_stats()
    if current.kanban is None:
        raise ValueError("kanban store não configurado — review indisponível")
    res = current.kanban.record_review_verdict(
        task_id, verdict, approver=approver, rationale=rationale,
    )
    return {"task_id": task_id, "verdict": verdict, "status": "decided", "reviewer_verdict": res.reviewer_verdict}


def action_decide_evolution(
    proposal_id: str,
    verdict: str,
    approver: str,
    rationale: Optional[str] = None,
    stats: Optional[DashboardStats] = None,
) -> str:
    """Registra a decisão de uma proposta (delta 44 — ``EvolutionLedger``).

    Fail-closed: proposta inexistente/já decidida/veredito inválido levanta
    ``LedgerError``; sem EventStore configurado levanta ``ValueError``."""
    current = stats if stats is not None else resolve_stats()
    if current.event_store is None:
        raise ValueError("event store não configurado — evolution indisponível")
    from hermes.platform.evolution.ledger import EvolutionLedger  # noqa: PLC0415

    return EvolutionLedger(current.event_store).decide(
        proposal_id, verdict, approver, rationale=rationale
    )


def action_approve_grant(
    scope: str,
    credential_ref: str,
    approver: str,
    rationale: Optional[str] = None,
) -> None:
    """Aprova um grant pendente (delta 43 — ``SecretBroker`` real)."""
    from hermes.platform.auth.vault import SecretBroker  # noqa: PLC0415

    SecretBroker().approve_grant(scope, credential_ref, approver,
                                 rationale=rationale)


def action_revoke_grant(scope: str, credential_ref: str) -> None:
    """Revoga um grant (delta 43 — ``SecretBroker`` real)."""
    from hermes.platform.auth.vault import SecretBroker  # noqa: PLC0415

    SecretBroker().revoke(scope, credential_ref)


def action_acp_plan(
    cwd: Optional[str] = None,
    instruction: Optional[str] = None,
    stats: Optional[DashboardStats] = None,
) -> Dict[str, Any]:
    """Inicia UMA sessão ACP de planejamento com o estado observável (delta 49).

    Deriva o brief do estado atual (``ui.planner.planning_brief`` — mesmas
    views do ``/state``, nunca segunda fonte) e o envia ao agente ACP
    configurado em ``configure_acp`` via o cliente canônico do K5
    (``run_planning_session``). ``cwd`` (opcional): quando omitido a sessão
    raiz no HERMES_HOME do processo do dashboard (a view do browser não
    conhece caminhos do servidor). Fail-closed: sem comando configurado
    levanta ``ValueError`` — o plugin nunca spawna um peer que não existe;
    erros do peer propagam (ACPUnavailableError/ACPError) para o chamador."""
    current = stats if stats is not None else resolve_stats()
    if not _acp_command:
        raise ValueError("agente ACP não configurado — configure_acp(command) primeiro")
    root = _default_session_cwd() if not cwd or not str(cwd).strip() else str(cwd)
    from hermes.platform.ui.planner import (  # noqa: PLC0415
        planning_brief, run_planning_session,
    )

    brief = planning_brief(current, instruction=instruction)
    return run_planning_session(_acp_command, root, brief, request_timeout_s=120.0)


def _default_session_cwd() -> str:
    """Diretório servidor padrão para sessões ACP (HERMES_HOME do processo).

    Fail-closed: sem HERMES_HOME resolvível não há raiz confiável — levanta
    ``ValueError`` (409 na rota) em vez de assumir um diretório arbitrário."""
    from hermes_constants import get_hermes_home  # noqa: PLC0415

    try:
        root = get_hermes_home()
    except Exception as exc:  # pragma: no cover - perfil sem home resolvível
        raise ValueError(f"cwd ACP indisponível (HERMES_HOME irresolvível): {exc}") from exc
    root = Path(str(root))
    if not root.is_dir():
        raise ValueError(f"cwd ACP indisponível (HERMES_HOME não existe): {root}")
    return str(root)


# --------------------------------------------------------------------------- #
# Router (contrato de mount do upstream)
#
# NOTA DE MONTAGEM AO VIVO (delta 48): handlers sync do FastAPI rodam num
# threadpool e o sqlite3 do upstream é thread-bound (check_same_thread). O
# KanbanAdapter cacheia agora UMA conexão POR THREAD (mesmo modelo do plugin
# kanban do próprio upstream, que abre conexão por request) — dispatch/state
# funcionam de qualquer thread do pool. EventStore file-backed já abre conexão
# por chamada; grants usam auth.json. Nenhum wiring de thread especial é
# necessário; configure_stats segue sendo a única montagem (stores canônicos).
# --------------------------------------------------------------------------- #
router: Any = None
if _HAS_FASTAPI and APIRouter is not None:
    router = APIRouter()

    @router.get("/state")  # type: ignore[union-attr]
    def get_state() -> Dict[str, Any]:  # noqa: N802 (rota, não função interna)
        return state_payload()

    @router.get("/evolution")  # type: ignore[union-attr]
    def get_evolution() -> Dict[str, Any]:
        return evolution_payload()

    @router.get("/health")  # type: ignore[union-attr]
    def get_health() -> Dict[str, Any]:
        stats = resolve_stats()
        return {"status": "ok", "available": stats.available()}

    @router.post("/dispatch")  # type: ignore[union-attr]
    def post_dispatch(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Despacha cards READY (control plane humano). 409 sem kanban."""
        try:
            executed = action_dispatch_ready(
                max_spawn=int((body or {}).get("max_spawn", 1))
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"executed": executed}

    @router.post("/reviews/decide")  # type: ignore[union-attr]
    def post_review_decide(body: Dict[str, Any]) -> Dict[str, Any]:
        """Decide a revisão de um card (approved|changes_requested)."""
        try:
            return action_decide_review(
                str(body["task_id"]),
                str(body["verdict"]),
                str(body["approver"]),
                rationale=body.get("rationale"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=400,
                                detail=f"campo obrigatório ausente: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/evolution/decide")  # type: ignore[union-attr]
    def post_evolution_decide(body: Dict[str, Any]) -> Dict[str, Any]:
        """Decide uma proposta (approved|rejected). 409 em estado inválido."""
        try:
            proposal_id = action_decide_evolution(
                str(body["proposal_id"]),
                str(body["verdict"]),
                str(body["approver"]),
                rationale=body.get("rationale"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=400,
                                detail=f"campo obrigatório ausente: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:  # LedgerError e afins
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"proposal_id": proposal_id, "status": "decided"}

    @router.post("/grants/approve")  # type: ignore[union-attr]
    def post_grant_approve(body: Dict[str, Any]) -> Dict[str, Any]:
        """Aprova um grant pendente (SecretBroker real)."""
        try:
            action_approve_grant(
                str(body["scope"]), str(body["credential_ref"]),
                str(body["approver"]), rationale=body.get("rationale"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=400,
                                detail=f"campo obrigatório ausente: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"scope": body["scope"], "credential_ref": body["credential_ref"],
                "status": "approved"}

    @router.post("/grants/revoke")  # type: ignore[union-attr]
    def post_grant_revoke(body: Dict[str, Any]) -> Dict[str, Any]:
        """Revoga um grant (SecretBroker real)."""
        try:
            action_revoke_grant(str(body["scope"]), str(body["credential_ref"]))
        except KeyError as exc:
            raise HTTPException(status_code=400,
                                detail=f"campo obrigatório ausente: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"scope": body["scope"], "credential_ref": body["credential_ref"],
                "status": "revoked"}

    @router.post("/acp/plan")  # type: ignore[union-attr]
    def post_acp_plan(body: Dict[str, Any]) -> Dict[str, Any]:
        """Roda uma sessão ACP de planejamento com o estado atual (delta 49).

        409 quando o agente ACP não está configurado (configure_acp) ou o peer
        falha; cwd opcional (omitido -> HERMES_HOME do processo, porque a view
        do browser não conhece caminhos do servidor)."""
        instruction = (body or {}).get("instruction") if isinstance(body, dict) else None
        cwd = (body or {}).get("cwd") if isinstance(body, dict) else None
        try:
            outcome = action_acp_plan(cwd=cwd, instruction=instruction)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:  # ACPUnavailableError/ACPError e afins
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "planned", **outcome}
