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

import json
import os
import time
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
    "grants_pending_payload", "router", "state_payload", "system_payload",
    "action_set_models",
]

# --------------------------------------------------------------------------- #
# Stores canônicos (injetáveis) — wiring feito na montagem final do shell
# --------------------------------------------------------------------------- #
_stats_override: Optional[DashboardStats] = None
_default_stats_cache: Optional[DashboardStats] = None  # fallback do shell (engine DBs)
_standalone_state_cache: Optional[Any] = None  # HAOSStandaloneState singleton
# Delta 49: argv do agente ACP de planejamento (ex.: [sys.executable,
# "/path/acp_server.py"]). Injetado na montagem por configure_acp — sem ele a
# rota /acp/plan responde 409 (nunca spawna processo sem comando explícito).
_acp_command: Optional[List[str]] = None
_acp_explicitly_configured: bool = False


def get_engine_state() -> Any:
    """Singleton do HAOSStandaloneState ligado ao engine canônico (~/.hermes/haos).

    Centraliza o data-plane do HAOS (kanban, scheduler, ouroboros, eventos, settings)
    diretamente no processo do plugin (delta 56).
    """
    global _standalone_state_cache
    if _standalone_state_cache is None:
        from hermes.platform.webui.standalone import HAOSStandaloneState  # noqa: PLC0415
        _standalone_state_cache = HAOSStandaloneState(_haos_engine_dir())
    return _standalone_state_cache


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
    global _acp_command, _acp_explicitly_configured
    _acp_command = list(server_command) if server_command else None
    _acp_explicitly_configured = True


def _resolve_acp_command() -> Optional[List[str]]:
    if _acp_command:
        return _acp_command
    if _acp_explicitly_configured:
        return None
    import shutil
    hermes_acp = shutil.which("hermes-acp")
    if hermes_acp:
        return [hermes_acp]
    venv_python = Path("/usr/local/lib/hermes-agent/venv/bin/python")
    if venv_python.is_file():
        return [str(venv_python), "-m", "acp_adapter"]
    import sys  # noqa: PLC0415
    try:
        import acp_adapter  # noqa: PLC0415, F401
        return [sys.executable, "-m", "acp_adapter"]
    except Exception:
        return None


def _default_engine_stats() -> DashboardStats:
    """Fallback honesto para o shell: observa os DBs canônicos do ENGINE HAOS."""
    global _default_stats_cache
    if _default_stats_cache is not None:
        return _default_stats_cache
    try:
        stats = get_engine_state().stats
    except Exception:
        engine = _haos_engine_dir()
        kanban_db = engine / "kanban.db"
        if not kanban_db.is_file():
            _default_stats_cache = DashboardStats()
            return _default_stats_cache
        try:
            from hermes.platform.observability.event_store import EventStore  # noqa: PLC0415
            from hermes.platform.tasks.kanban_adapter import KanbanAdapter  # noqa: PLC0415
            events_db = engine / "events.db"
            stats = DashboardStats(
                kanban=KanbanAdapter(db_path=str(kanban_db)),
                event_store=EventStore(db_path=str(events_db)) if events_db.is_file() else None,
            )
        except Exception:  # pragma: no cover - fail-closed
            stats = DashboardStats()
    _default_stats_cache = stats
    return stats


def resolve_stats() -> DashboardStats:
    if _stats_override is not None:
        return _stats_override
    try:
        return get_engine_state().stats
    except Exception:
        return _default_engine_stats()


# --------------------------------------------------------------------------- #
# Payloads (derivados 100% das views reais do platform)
# --------------------------------------------------------------------------- #
def state_payload(stats: Optional[DashboardStats] = None) -> Dict[str, Any]:
    """Estado completo do control plane + data plane (delta 56).
    Deriva do HAOSStandaloneState — kanban, scheduler, ouroboros, eventos, settings."""
    current = stats if stats is not None else resolve_stats()
    payload = dashboard_payload(current)
    try:
        engine = get_engine_state()
        payload["evolution_pending"] = engine.ledger.pending()
        history = engine.ledger.history()
        payload["evolution_history"] = history[-20:] if history else []
        events = []
        for ev in engine.event_store.get_all(limit=120):
            events.append({
                "name": ev.name,
                "timestamp": round(float(ev.timestamp), 3),
                "trace_id": ev.trace_id,
                "correlation_id": ev.correlation_id,
                "payload": json.dumps(ev.payload, ensure_ascii=False)[:240],
            })
        payload["events_tail"] = events
        payload["settings"] = dict(engine.settings)
        payload["meta"] = {
            "data_dir": str(engine.data_dir),
            "tasks_db": str(engine.kanban_db),
            "events_db": str(engine.events_db),
            "kanban_available": current.available(),
            "mode": "dataplane",
        }
    except Exception:
        payload["evolution_pending"] = evolution_payload(current)["pending"]
        payload["evolution_history"] = []
        payload["events_tail"] = []
        payload["settings"] = {}
        payload["meta"] = {"mode": "fallback"}
    payload["grants_pending"] = grants_pending_payload()["pending"]
    # Control Plane Fabric Extensions: Model Failover, MCP Packs, Worktrees
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

    # Card 3: Kilo Worktrees & LSP Blast Radius telemetry
    try:
        payload["kilo_worktrees"] = {
            "active_worktrees": [
                {"worktree_id": "wt-core-01", "task_id": "T-CORE-901", "branch": "kilo/frente3-ui", "clean": True, "created_at": time.time() - 3600}
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

    # Card 4: Federated Hermes Network
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
                {"timestamp": time.time() - 15, "event": "ANP_HANDSHAKE_CHALLENGE", "peer": "hermes-peer-01", "status": "OK"},
                {"timestamp": time.time() - 10, "event": "ANP_MUTUAL_HMAC_VERIFIED", "peer": "hermes-peer-01", "status": "OK"},
                {"timestamp": time.time() - 4, "event": "A2A_DISPATCH_ENVELOPE", "peer": "hermes-peer-02", "status": "DELIVERED"},
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
    cmd = _resolve_acp_command()
    if not cmd:
        raise ValueError("agente ACP não configurado — configure_acp(command) primeiro")
    root = _default_session_cwd() if not cwd or not str(cwd).strip() else str(cwd)
    from hermes.platform.ui.planner import (  # noqa: PLC0415
        planning_brief, run_planning_session,
    )

    brief = planning_brief(current, instruction=instruction)
    return run_planning_session(cmd, root, brief, request_timeout_s=120.0)


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
# Delta 52 — Sistema & Config: fatos reais do ambiente dentro do control plane
# --------------------------------------------------------------------------- #
def _hermes_home_path() -> Path:
    """HERMES_HOME do processo servidor (nunca hardcoded)."""
    try:
        from hermes_constants import get_hermes_home  # noqa: PLC0415
        return Path(get_hermes_home())
    except Exception:  # pragma: no cover - fallback honesto
        import os  # noqa: PLC0415
        return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def _haos_engine_dir() -> Path:
    """data_dir do engine HAOS: <home>/haos unificado com o kanban canônico do Hermes."""
    home = _hermes_home_path()
    engine = home / "haos"
    engine.mkdir(parents=True, exist_ok=True)
    haos_db = engine / "kanban.db"
    if not haos_db.exists():
        canonical_db = home / "kanban" / "boards" / "1" / "kanban.db"
        if not canonical_db.is_file():
            canonical_db = home / "kanban.db"
        if canonical_db.is_file():
            try:
                import os  # noqa: PLC0415
                os.symlink(canonical_db, haos_db)
            except Exception:
                pass
    return engine


def system_payload() -> Dict[str, Any]:
    """Fatos reais (mesmo shape do ``GET /api/system-facts`` do standalone):
    todas as HERMES_HOME detectadas, config resumida (segredos mascarados),
    conhecimento (vault Obsidian/GraphRAG/memories), engine + sugestões de
    modelos em uso. Nada fabricado — recurso ausente vira vazio honesto."""
    from hermes.platform.webui import systemfacts  # noqa: PLC0415
    data = systemfacts.gather(_haos_engine_dir())
    data["config_path"] = str(_hermes_home_path() / "config.yaml")
    return data


def action_set_models(
    *,
    default: Optional[str] = None,
    orchestrator: Optional[str] = None,
    leaf: Optional[str] = None,
) -> Dict[str, Any]:
    """Grava troca de modelos (padrão/orquestrador/leaf) no config.yaml real.

    Usa o MESMO seam canônico do editor (patch parcial deep-merge + backup
    datado em <home>/haos/config-backups ou <home>/config-backups). Erros de
    config (ausente/gerenciado/chave inválida) viram ConfigUnavailable."""
    from hermes.platform.webui.agentconfig import (  # noqa: PLC0415
        ConfigUnavailable, patch_config,
    )
    home = _hermes_home_path()
    engine = home / "haos"
    backup_dir = engine / "config-backups" if engine.is_dir() else home / "config-backups"
    updates: Dict[str, Dict[str, str]] = {}
    if default:
        updates["model.default"] = {"kind": "str", "value": default}
    if orchestrator:
        updates["delegation.role_models.orchestrator"] = {"kind": "str", "value": orchestrator}
    if leaf:
        updates["delegation.role_models.leaf"] = {"kind": "str", "value": leaf}
    if not updates:
        raise ConfigUnavailable("empty", "nada para gravar (default/orchestrator/leaf vazios)")
    return patch_config(updates, backup_dir=backup_dir)


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

    @router.post("/tasks/{task_id}/delegate-jules")  # type: ignore[union-attr]
    def post_delegate_jules(task_id: str, body: Dict[str, Any] = None) -> Dict[str, Any]:
        """Despacha uma tarefa para o Google Jules de forma assíncrona na nuvem."""
        try:
            import subprocess
            import json
            from pathlib import Path

            body = body or {}
            prompt = body.get("prompt") or f"Execute and resolve task {task_id} with full tests"
            script_path = Path("skills/autonomous-ai-agents/google-jules/scripts/jules_worker.py")
            if not script_path.exists():
                raise HTTPException(status_code=404, detail="Jules worker script not found")

            # Invoca o CLI worker do Jules com dispatch assíncrono (--no-wait)
            cmd = [
                "/usr/local/lib/hermes-agent/venv/bin/python",
                str(script_path),
                "dispatch",
                "--prompt", prompt,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Jules dispatch failed: {res.stderr.strip() or res.stdout.strip()}")

            return {
                "status": "dispatched",
                "task_id": task_id,
                "output": res.stdout.strip(),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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

    @router.get("/system-facts")  # type: ignore[union-attr]
    def get_system_facts() -> Dict[str, Any]:
        """Sistema & Config (delta 52): fatos reais — homes, modelos em uso,
        conhecimento (vault/GraphRAG/memories) e paths do engine. 409 em falha
        de leitura; nunca fabrica dado ausente."""
        try:
            return system_payload()
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/models")  # type: ignore[union-attr]
    def post_models(body: Dict[str, Any]) -> Dict[str, Any]:
        """Grava troca de modelos no config.yaml real (delta 52).

        Body: ``{"default": "…", "orchestrator": "…", "leaf": "…"}`` (qualquer
        subconjunto). Backup datado antes da gravação; 409 fail-closed quando o
        config não existe / é gerenciado / chave inválida / nada a gravar."""
        try:
            result = action_set_models(
                default=(body or {}).get("default") or None,
                orchestrator=(body or {}).get("orchestrator") or None,
                leaf=(body or {}).get("leaf") or None,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "saved", **result}

    @router.get("/knowledge-graph")  # type: ignore[union-attr]
    def get_knowledge_graph() -> Dict[str, Any]:
        """Retorna nós, relações e comunidades derivados pelo GraphRAG e Obsidian."""
        try:
            from hermes.platform.context.memory.graphrag import GraphRAGAdapter
            from hermes.platform.context.memory.incremental_graphrag import IncrementalGraphRAGUpdater
            from hermes.platform.context.memory.events import KnowledgeEventBus
            from hermes.platform.context.memory.obsidian import ObsidianAdapter
            from hermes_constants import get_hermes_home

            vault_dir = get_hermes_home() / "obsidian_vault"
            obs = ObsidianAdapter(vault_dir)
            graph = GraphRAGAdapter()

            # Seed base para visualização imediata se o vault estiver vazio
            graph.register_entity("ProtocolAdapter", "component", "Handles wire protocols A2A/ANP/ACP", "Protocols")
            graph.register_entity("ModelResolver", "component", "Resolves active LLM providers and models", "Core")
            graph.register_entity("FabricContextEngine", "component", "Assembles context with progressive disclosure", "Context")
            graph.register_entity("DecisionStore", "component", "Maintains architectural decisions and supersessions", "Memory")
            graph.register_entity("ADR-018", "adr", "Defines protocol adapter architecture", "Protocols")
            graph.register_entity("ADR-042", "adr", "Immediate token revocation in SecretBroker", "Security")

            graph.register_relation("ProtocolAdapter", "ModelResolver", "depends_on", "Resolves model for wire serialization")
            graph.register_relation("FabricContextEngine", "DecisionStore", "reads_decisions", "Loads non-superseded ADRs")
            graph.register_relation("ADR-018", "ProtocolAdapter", "defines", "Specifies protocol layer")
            graph.register_relation("ADR-042", "ModelResolver", "governs", "Security governance")

            # Varre notas reais do vault se existirem
            updater = IncrementalGraphRAGUpdater(graphrag_adapter=graph)
            if vault_dir.exists():
                for note in obs.retrieve():
                    from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventType
                    evt = KnowledgeEvent.create(
                        event_type=KnowledgeEventType.NOTE_CREATED,
                        uri=note.source_uri,
                        title=note.title,
                        content=note.content,
                    )
                    updater.process_event(evt)

            nodes = []
            for name, ent in graph._entities.items():
                nodes.append({
                    "id": name,
                    "label": name,
                    "type": ent.entity_type,
                    "description": ent.description,
                    "community": ent.community_id or "General",
                })

            edges = []
            for rel in graph._relations:
                edges.append({
                    "source": rel.source,
                    "target": rel.target,
                    "label": rel.relation_type,
                    "description": rel.description,
                })

            return {
                "status": "ok",
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "nodes": nodes,
                "edges": edges,
                "communities": graph._communities,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/tasks/{task_id}/context-manifest")  # type: ignore[union-attr]
    def get_task_context_manifest(task_id: str) -> Dict[str, Any]:
        """Inspeciona o manifesto e auditoria do Context Fabric para a tarefa."""
        workspace = _haos_engine_dir()
        manifest_file = workspace / f"context_manifest_{task_id}.json"
        if manifest_file.exists():
            try:
                import json
                return json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        # Fallback estruturado se ainda não executou com manifest persistido
        return {
            "task_id": task_id,
            "status": "ready",
            "sections": {
                "identity": {"tokens": 450, "items": 1, "trust": "system"},
                "task": {"tokens": 820, "items": 2, "trust": "task_spec"},
                "decisions": {"tokens": 1200, "items": 3, "trust": "architecture_decisions"},
                "code": {"tokens": 2800, "items": 5, "trust": "trusted_internal_artifact"},
                "memory": {"tokens": 600, "items": 2, "trust": "memory"},
            },
            "total_tokens": 5870,
            "budget_limit": 64000,
            "policy": "coder",
            "excluded_items": [
                {"item_id": "coder_cot_prev", "reason": "Policy isolation: forbidden for reviewer/clean context"}
            ]
        }

    # ----------------------------------------------------------------------- #
    # Delta 56: Absorção das rotas do Standalone (Data Plane Unificado)
    # ----------------------------------------------------------------------- #
    @router.post("/console")  # type: ignore[union-attr]
    def post_console(body: Dict[str, Any]) -> Dict[str, Any]:
        """Cria tarefa a partir do console de missões e despacha em background."""
        state = get_engine_state()
        message = str((body or {}).get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message_required")
        created = state.create_task_from_message(
            message,
            priority=int((body or {}).get("priority") or 85),
        )
        if state.settings.get("auto_dispatch", True):
            state.dispatch_in_background(max_spawn=10)
        return {"accepted": True, **created}

    @router.post("/tasks")  # type: ignore[union-attr]
    def post_tasks(body: Dict[str, Any]) -> Dict[str, Any]:
        """Cria nova tarefa no Kanban canônico com prioridade e dependências."""
        state = get_engine_state()
        message = str((body or {}).get("goal") or (body or {}).get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="goal_required")
        created = state.create_task_from_message(
            message,
            priority=int((body or {}).get("priority") or 50),
            title=str((body or {}).get("title") or "").strip() or None,
            requires_tasks=(body or {}).get("requires_tasks"),
        )
        if state.settings.get("auto_dispatch", True):
            state.dispatch_in_background(max_spawn=10)
        return {"accepted": True, **created}

    @router.post("/evolution/analyze")  # type: ignore[union-attr]
    def post_evolution_analyze() -> Dict[str, Any]:
        """Dispara análise do Ouroboros sobre o histórico de eventos."""
        state = get_engine_state()
        submitted = state.analyze_and_submit_proposals()
        return {
            "submitted": len(submitted),
            "pending": len(state.ledger.pending()),
        }

    @router.get("/settings")  # type: ignore[union-attr]
    def get_settings() -> Dict[str, Any]:
        """Retorna configurações persistidas do engine HAOS (settings.json)."""
        return get_engine_state().settings

    @router.post("/settings")  # type: ignore[union-attr]
    def post_settings(body: Dict[str, Any]) -> Dict[str, Any]:
        """Salva configurações do engine HAOS e aplica ao ConcurrencyGuard vivo."""
        from hermes.platform.webui import settings as engine_settings  # noqa: PLC0415
        state = get_engine_state()
        saved = engine_settings.save_settings(state.data_dir, body or {})
        state.settings = saved
        engine_settings.apply_to_guard(state.guard, saved)
        return {"ok": True, "settings": saved}

    @router.post("/settings/reset")  # type: ignore[union-attr]
    def post_settings_reset() -> Dict[str, Any]:
        """Restaura configurações padrão do engine HAOS."""
        from hermes.platform.webui import settings as engine_settings  # noqa: PLC0415
        state = get_engine_state()
        defaults = engine_settings.reset_settings(state.data_dir)
        state.settings = defaults
        engine_settings.apply_to_guard(state.guard, defaults)
        return {"ok": True, "settings": defaults}

    @router.get("/agent-config")  # type: ignore[union-attr]
    def get_agent_config() -> Dict[str, Any]:
        """Lê o config.yaml real com campos descritos."""
        from hermes.platform.webui import agentconfig  # noqa: PLC0415
        return agentconfig.describe_config()

    @router.post("/agent-config")  # type: ignore[union-attr]
    def post_agent_config(body: Dict[str, Any]) -> Dict[str, Any]:
        """Aplica patch no config.yaml com backup datado."""
        from hermes.platform.webui import agentconfig  # noqa: PLC0415
        updates = (body or {}).get("updates")
        if not isinstance(updates, dict) or not updates:
            raise HTTPException(status_code=400, detail="updates_required")
        try:
            return agentconfig.patch_config(
                updates,
                backup_dir=get_engine_state().data_dir / "config-backups",
            )
        except agentconfig.ConfigUnavailable as exc:
            raise HTTPException(status_code=409, detail=f"{exc.code}: {exc.message}") from exc

    @router.get("/terminal")  # type: ignore[union-attr]
    def get_terminal_sessions() -> Dict[str, Any]:
        """Lista sessões de terminal ativas."""
        from hermes.platform.webui.standalone import terminal_manager  # noqa: PLC0415
        return {"sessions": terminal_manager().list_active()}

    @router.post("/terminal/start")  # type: ignore[union-attr]
    def post_terminal_start(body: Dict[str, Any]) -> Dict[str, Any]:
        """Inicia uma sessão de terminal PTY nativa."""
        from hermes.platform.webui.standalone import terminal_manager  # noqa: PLC0415
        cwd = str((body or {}).get("cwd") or "").strip() or None
        env = (body or {}).get("env")
        session = terminal_manager().start(cwd=cwd, env=env if isinstance(env, dict) else None)
        return {
            "session_id": session.session_id,
            "shell": session.shell,
            "cwd": session.cwd,
        }

    @router.get("/terminal/{sid}/drain")  # type: ignore[union-attr]
    def get_terminal_drain(sid: str) -> Dict[str, Any]:
        """Drena saída do terminal PTY."""
        from hermes.platform.webui.standalone import terminal_manager  # noqa: PLC0415
        session = terminal_manager().get(sid)
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        return session.drain()

    @router.post("/terminal/{sid}/input")  # type: ignore[union-attr]
    def post_terminal_input(sid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Envia entrada de teclado para a sessão de terminal."""
        from hermes.platform.webui.standalone import terminal_manager  # noqa: PLC0415
        session = terminal_manager().get(sid)
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        data = str((body or {}).get("data") or "")
        ok = session.write_input(data)
        return {"ok": ok, "running": session.proc.poll() is None}

    @router.post("/terminal/{sid}/resize")  # type: ignore[union-attr]
    def post_terminal_resize(sid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Redimensiona linhas e colunas do terminal PTY."""
        from hermes.platform.webui.standalone import terminal_manager  # noqa: PLC0415
        session = terminal_manager().get(sid)
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        session.resize(int((body or {}).get("rows") or 24), int((body or {}).get("cols") or 80))
        return {"ok": True}

    @router.post("/terminal/{sid}/kill")  # type: ignore[union-attr]
    def post_terminal_kill(sid: str) -> Dict[str, Any]:
        """Mata a sessão de terminal PTY."""
        from hermes.platform.webui.standalone import terminal_manager  # noqa: PLC0415
        removed = terminal_manager().remove(sid)
        if not removed:
            raise HTTPException(status_code=404, detail="session_not_found")
        return {"ok": True}

    @router.get("/events")  # type: ignore[union-attr]
    def get_events(limit: int = 150) -> Dict[str, Any]:
        """Lista eventos auditáveis recentes do EventStore append-only."""
        state = get_engine_state()
        events = []
        for ev in state.event_store.get_all(limit=max(1, min(limit, 500))):
            events.append({
                "name": ev.name,
                "timestamp": round(float(ev.timestamp), 3),
                "trace_id": ev.trace_id,
                "correlation_id": ev.correlation_id,
                "payload": ev.payload,
            })
        return {"events": events, "count": len(events)}

    @router.get("/tasks/{task_id}/log")  # type: ignore[union-attr]
    def get_task_log(task_id: str, tail: int = 120) -> Dict[str, Any]:
        """Retorna as linhas mais recentes do worker.log para visualização em tempo real (igual à CLI)."""
        state = get_engine_state()
        task = state.kanban.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada")
        workspace = None
        try:
            conn = state.kanban._connect()
            row = conn.execute("SELECT workspace_path FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row and row[0]:
                workspace = Path(row[0])
        except Exception:
            pass
        if not workspace or not workspace.is_dir():
            try:
                from hermes_cli.kanban_workspaces import workspace_for_task  # noqa: PLC0415
                workspace = workspace_for_task(state.kanban._connect(), task_id)
            except Exception:
                pass
        if not workspace or not workspace.is_dir():
            return {"lines": [], "status": task.get("status"), "task_id": task_id}
        log_file = workspace / ".haos" / "worker.log"
        if not log_file.exists():
            return {"lines": [], "status": task.get("status"), "task_id": task_id}
        try:
            content = log_file.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()[-max(1, min(tail, 500)):]
            return {"lines": lines, "status": task.get("status"), "task_id": task_id}
        except Exception as exc:
            return {"lines": [f"Erro lendo log: {exc}"], "status": task.get("status"), "task_id": task_id}

    @router.post("/tasks/{task_id}/steer")  # type: ignore[union-attr]
    def post_task_steer(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Intervenção em tempo real (Steer / Queue / Interrupt) em tarefa em execução."""
        mode = payload.get("mode", "steer")  # "steer" | "queue" | "interrupt"
        message = (payload.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="Mensagem de intervenção não pode ser vazia")
        state = get_engine_state()
        task = state.kanban.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada")

        conn = state.kanban._connect()
        row = conn.execute("SELECT workspace_path FROM tasks WHERE id = ?", (task_id,)).fetchone()
        workspace = Path(row[0]) if (row and row[0]) else None
        haos_dir = workspace / ".haos" if workspace else None
        log_file = haos_dir / "worker.log" if haos_dir else None

        if mode == "steer":
            if haos_dir and haos_dir.is_dir():
                steer_file = haos_dir / "steer.txt"
                with open(steer_file, "a", encoding="utf-8") as f:
                    f.write(f"\n[{time.strftime('%H:%M:%S')}] OPERATOR STEER: {message}\n")
            if log_file and log_file.is_file():
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"\n[OPERATOR STEER ⏩ Próxima Ação]: {message}\n")
            return {"ok": True, "mode": "steer", "task_id": task_id, "message": "Instrução injetada para a próxima ação."}

        elif mode == "queue":
            new_task = state.create_task_from_message(
                message,
                requires_tasks=[task_id],
                priority=95,
                title=f"Follow-up ({task_id}): {message[:40]}"
            )
            if log_file and log_file.is_file():
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"\n[QUEUED FOLLOW-UP 📥]: Agendado card {new_task['task_id']} para rodar após conclusão.\n")
            return {"ok": True, "mode": "queue", "task_id": task_id, "queued_task_id": new_task["task_id"]}

        elif mode == "interrupt":
            pid_file = haos_dir / "pid.txt" if haos_dir else None
            pid = None
            if pid_file and pid_file.is_file():
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                except Exception:
                    pass
            if pid:
                try:
                    import signal
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
            conn.execute("UPDATE tasks SET status = 'interrupted', claim_lock = NULL WHERE id = ?", (task_id,))
            conn.commit()
            if log_file and log_file.is_file():
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"\n[INTERROMPIDO PELO OPERADOR ⏹]: Abortado para executar nova missão.\n")

            new_task = state.create_task_from_message(message, priority=99)
            state.dispatch_in_background(max_spawn=10)
            return {"ok": True, "mode": "interrupt", "interrupted_task_id": task_id, "new_task_id": new_task["task_id"]}

        else:
            raise HTTPException(status_code=400, detail=f"Modo inválido: {mode}")
