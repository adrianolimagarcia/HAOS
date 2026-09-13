"""HAOS Standalone WebUI — servidor HTTP canônico (produto próprio do HAOS).

A UI standalone do HAOS é um servidor stdlib (``ThreadingHTTPServer``) que
serve a interface web do próprio HAOS — Console de missões, Taskboard,
Scheduler (CPM/PIP), ConcurrencyGuard, Ouroboros, Eventos e Configurações do
engine — sempre DERIVADA dos stores canônicos persistentes do platform
(KanbanAdapter + EventStore + ConcurrencyGuard + EvolutionLedger), nunca
fabricada.

Diferente do ``ui/server.py`` (demo derivado, sem persistência) e do plugin
montado no dashboard oficial (host adapter do shell), este servidor é a
**superfície standalone**: cria o próprio ``data_dir`` persistente
(default ``~/.haos``), mantém os DBs canônicos e executa ações reais
de control plane (criar card, despachar na lane canônica, decidir propostas
Ouroboros, aplicar configurações do engine AO VIVO).

Aba de Chat (Console): cada mensagem do operador vira um ``TaskSpec``
submetido ao Task Engine (``Task != Run``) e é despachado pela lane canônica;
a UI mostra o ciclo de vida real (READY -> RUNNING -> DONE/falha classificada).
Sem modelo/provider configurado a lane falha com categoria honesta — nunca
finge resposta.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class HAOSThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        super().server_bind()
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

# Garante que a raiz do repositório esteja no sys.path para execução direta como script
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.sink import EventStoreSink
from hermes.platform.execution.backpressure import ConcurrencyGuard
from hermes.platform.execution.dispatcher import HAOSDispatcher
from hermes.platform.evolution.ledger import EvolutionLedger
from hermes.platform.evolution.analyzer import OuroborosAnalyzer
from hermes.platform.ui.dashboard import dashboard_payload
from hermes.platform.ui.stats import DashboardStats
from hermes.platform.webui.controlplane import ControlPlaneService

from hermes.platform.webui import settings as engine_settings

_DEFAULT_PORT = 8788
_STATIC_DIR = Path(__file__).parent / "static"


def _jsonable(obj: Any) -> Any:
    """Serializador JSON para objetos do platform (TaskRun/TaskResult/etc.)."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj, (dict, list, tuple)):
        return obj
    return str(obj)


class HAOSStandaloneState:
    """Stores canônicos do standalone (thread-safe, conexão por thread)."""

    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        self.kanban_db = data_dir / "kanban.db"
        self.events_db = data_dir / "events.db"

        self.event_store = EventStore(str(self.events_db))
        self.event_sink = EventStoreSink(self.event_store)
        self.kanban = KanbanAdapter(self.kanban_db, event_sink=self.event_sink)
        self.guard = ConcurrencyGuard()
        self.stats = DashboardStats(
            kanban=self.kanban,
            event_store=self.event_store,
            concurrency_guard=self.guard,
        )
        self.dispatcher = HAOSDispatcher(self.kanban, concurrency_guard=self.guard)
        self.ledger = EvolutionLedger(self.event_store)
        self.analyzer = OuroborosAnalyzer()
        self.control_plane = ControlPlaneService(self.event_store, kanban=self.kanban)

        # Aplica settings persistidos (defaults se ausente) ao guard vivo.
        self.settings = engine_settings.load_settings(data_dir)
        engine_settings.apply_to_guard(self.guard, self.settings)

        # No servidor ao vivo (fora da suíte rápida de testes unitários),
        # instala os workers agênticos reais para que tarefas executem o agente real.
        if "PYTEST_CURRENT_TEST" not in os.environ and not os.environ.get("HERMES_DETERMINISTIC_TESTS"):
            try:
                from hermes.platform.execution.lane_executor import install_real_lane_workers
                install_real_lane_workers()
            except Exception:
                pass

        # Serializa despachos (console/taskboard) em background thread.
        self._dispatch_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def create_task_from_message(
        self,
        message: str,
        *,
        priority: int = 85,
        title: Optional[str] = None,
        requires_tasks: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        spec = TaskSpec(
            id=f"T-{uuid.uuid4().hex[:6]}",
            title=title or f"Missão: {message[:60]}",
            goal=message,
            priority=int(priority),
            posture="implementer",
            model_profile=None,
            requires_tasks=[str(t) for t in (requires_tasks or []) if t],
            workspace_type="scratch",
        )
        task_id = self.kanban.save_task(spec, status="READY")
        return {"task_id": task_id, "spec_id": spec.id, "goal": message}

    def dispatch_in_background(self, *, max_spawn: int = 10) -> bool:
        """Dispara claim_tick canônico em cascata em thread daemon (nunca bloqueia o HTTP)."""
        def _run() -> None:
            try:
                with self._dispatch_lock:
                    remaining = int(max_spawn)
                    while remaining > 0:
                        executed = self.dispatcher.claim_tick(
                            worker_id="haos-webui",
                            max_spawn=1,
                        )
                        if not executed:
                            break
                        remaining -= 1
            except Exception:  # noqa: BLE001 - falha registrada no card pela lane
                pass
        threading.Thread(target=_run, daemon=True).start()
        return True

    # ------------------------------------------------------------------ #
    def state_payload(self) -> Dict[str, Any]:
        payload = dashboard_payload(self.stats)
        payload["evolution_pending"] = self.ledger.pending()
        history = self.ledger.history()
        payload["evolution_history"] = history[-20:]
        events: List[Any] = []
        for ev in self.event_store.get_all(limit=120):
            events.append({
                "name": ev.name,
                "timestamp": round(float(ev.timestamp), 3),
                "trace_id": ev.trace_id,
                "payload": json.dumps(ev.payload, ensure_ascii=False)[:240],
            })
        payload["events_tail"] = events
        payload["team_graph"] = self.control_plane.get_team_graph_snapshot()
        try:
            payload["control_overview"] = dataclasses.asdict(self.control_plane.get_overview())
        except Exception:
            payload["control_overview"] = {}
        payload["settings"] = dict(self.settings)
        payload["meta"] = {
            "data_dir": str(self.data_dir),
            "tasks_db": str(self.kanban_db),
            "events_db": str(self.events_db),
            "kanban_available": self.stats.available(),
            "mode": "standalone",
        }
        return payload

    def analyze_and_submit_proposals(self) -> List[Dict[str, Any]]:
        """Roda o Ouroboros em shadow mode e submete propostas ao ledger."""
        proposals = self.analyzer.analyze_execution_history(event_store=self.event_store)
        submitted = []
        for proposal in proposals or []:
            try:
                self.ledger.submit(proposal)
                submitted.append(proposal)
            except Exception:  # noqa: BLE001 - dedupe/estado já submetido
                continue
        return submitted

    def get_task_details(self, task_id: str) -> Optional[Dict[str, Any]]:
        task = self.kanban.get_task(task_id)
        if not task:
            return None
        events = self.kanban.list_run_events(task_id)
        ws_path = task.get("workspace_path")
        log_content = ""
        pid = None
        if ws_path:
            p = Path(ws_path)
            log_file = p / ".haos" / "worker.log"
            pid_file = p / ".haos" / "pid.txt"
            if pid_file.is_file():
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                except Exception:
                    pass
            if log_file.is_file():
                try:
                    size = log_file.stat().st_size
                    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                        if size > 65536:
                            f.seek(size - 65536)
                        log_content = f.read()
                except Exception:
                    pass
        return {
            **task,
            "events": events,
            "log_tail": log_content,
            "pid": pid,
        }

    def cancel_task(self, task_id: str, reason: str = "Interrompido pelo operador via UI") -> bool:
        task = self.kanban.get_task(task_id)
        if not task:
            return False
        ws_path = task.get("workspace_path")
        if ws_path:
            pid_file = Path(ws_path) / ".haos" / "pid.txt"
            if pid_file.is_file():
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                    import signal
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
        try:
            self.kanban.record_task_failure(task_id, reason, outcome="user_canceled")
        except Exception:
            pass
        return True

    def requeue_task(self, task_id: str) -> bool:
        conn = self.kanban._connect()
        conn.execute("UPDATE tasks SET status = 'ready', claim_lock = NULL, started_at = NULL WHERE id = ?", (task_id,))
        conn.commit()
        return True

    def clear_completed_tasks(self, include_failed: bool = False) -> int:
        return self.kanban.clear_completed_tasks(include_failed=include_failed)

    def delete_task(self, task_id: str) -> bool:
        return self.kanban.delete_task(task_id)

    def reset_all_tasks(self) -> int:
        return self.kanban.reset_all_tasks()

    def get_unified_timeline(self, limit: int = 150, category: Optional[str] = None) -> List[Dict[str, Any]]:
        timeline = []
        raw_events = self.event_store.get_all(limit=limit)
        for ev in raw_events:
            p = ev.payload or {}
            name = str(ev.name)
            ts = float(ev.timestamp)
            cat = "system"
            icon = "⚡"
            title = name

            if name.startswith("haos.task.") or name.startswith("task."):
                cat = "tasks"
                icon = "📋"
                if "spawned" in name or "started" in name:
                    title = f"Missão iniciada: {p.get('task_id') or p.get('goal') or ''}"
                    icon = "🚀"
                elif "completed" in name:
                    title = f"Missão concluída: {p.get('task_id') or ''}"
                    icon = "✅"
                elif "failed" in name:
                    title = f"Falha na missão: {p.get('task_id') or p.get('error') or ''}"
                    icon = "❌"
            elif "tool" in name:
                cat = "tools"
                icon = "🔧"
                title = f"Tool chamada: {p.get('tool') or p.get('name') or name}"
            elif "intervention" in name:
                cat = "interventions"
                icon = "🛡️"
                title = f"Intervenção de operador: {p.get('target_id')} -> {p.get('action')}"
            elif "evolution" in name or "proposal" in name:
                cat = "ouroboros"
                icon = "🔄"
                title = f"Evolução de código: {p.get('proposal_id') or name}"
            elif "model" in name or "route" in name:
                cat = "agent"
                icon = "🧠"
                title = f"Roteamento de IA: {p.get('model') or name}"

            if category and category != "all" and cat != category:
                continue

            timeline.append({
                "id": ev.event_id,
                "name": name,
                "category": cat,
                "icon": icon,
                "title": title,
                "timestamp": ts,
                "trace_id": ev.trace_id,
                "correlation_id": ev.correlation_id,
                "payload": p,
            })

        timeline.sort(key=lambda x: x["timestamp"], reverse=True)
        return timeline[:limit]


class HAOSStandaloneHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, state: Optional[HAOSStandaloneState] = None,
                 title: str = "HAOS Standalone", **kwargs):
        # O __init__ do BaseHTTPRequestHandler JÁ processa o primeiro request
        # (handle_one_request) — o state precisa existir ANTES do super().__init__.
        default_dir = Path(os.environ.get("HAOS_DATA_DIR") or os.environ.get("HAOS_HOME", Path.home() / ".haos"))
        self.state = state or HAOSStandaloneState(default_dir)
        self._title = title
        super().__init__(*args, **kwargs)

    # ------------------------------------------------------------------ #
    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: Any) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False,
                                    default=_jsonable).encode("utf-8"))

    def _read_json_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def log_message(self, *args) -> None:  # noqa: N802 - silencioso
        pass

    def do_GET(self) -> None:  # noqa: N802
        self._serve()

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve()

    def do_POST(self) -> None:  # noqa: N802
        self._serve()

    # ------------------------------------------------------------------ #
    def _serve(self) -> None:
        path = urlparse(self.path).path
        if self.command in ("GET", "HEAD") and (path in ("/", "/index.html", "/chat", "/console", "/terminal", "/taskboard", "/scheduler", "/ouroboros", "/agent", "/system", "/events", "/config") or not path.startswith(("/api/", "/health", "/v1/"))):
            self._serve_index()
        elif self.command == "GET" and path in ("/health", "/api/health"):
            self._send_json(200, {"status": "healthy", "service": "haos-controlplane"})
        elif self.command == "GET" and path == "/api/state":
            self._send_json(200, self.state.state_payload())
        elif self.command == "GET" and path in ("/v1/models", "/api/v1/models"):
            self._handle_v1_models()
        elif self.command == "GET" and path in ("/api/team-graph", "/api/controlplane/team_graph"):
            self._send_json(200, self.state.control_plane.get_team_graph_snapshot())
        elif self.command == "GET" and path in ("/api/controlplane/overview", "/api/overview"):
            self._send_json(200, dataclasses.asdict(self.state.control_plane.get_overview()))
        elif self.command == "POST" and path in ("/api/intervene", "/api/controlplane/intervene"):
            self._intervene()
        elif self.command == "GET" and path == "/api/agent-settings":
            self._agent_settings()
        elif self.command == "POST" and path == "/api/console":
            self._console()
        elif self.command == "POST" and path == "/api/tasks":
            self._create_task()
        elif self.command == "POST" and path in ("/api/tasks/clear", "/api/tasks/clear-completed"):
            self._clear_tasks()
        elif self.command == "POST" and path == "/api/dispatch":
            self._dispatch()
        elif self.command == "POST" and path == "/api/evolution/analyze":
            self._evolution_analyze()
        elif self.command == "POST" and path == "/api/evolution/decide":
            self._evolution_decide()
        elif self.command == "POST" and path == "/api/evolution/blast-radius":
            self._evolution_blast_radius()
        elif self.command == "POST" and path == "/api/evolution/automerge":
            self._evolution_automerge()
        elif self.command == "GET" and path == "/api/settings":
            self._send_json(200, self.state.settings)
        elif self.command == "POST" and path == "/api/settings":
            self._save_settings()
        elif self.command == "POST" and path == "/api/settings/reset":
            self._reset_settings()
        elif self.command == "GET" and path == "/api/agent-config":
            self._agent_config_read()
        elif self.command == "POST" and path == "/api/agent-config":
            self._agent_config_patch()
        elif self.command == "GET" and path == "/api/sessions":
            self._sessions_list()
        elif self.command == "GET" and path == "/api/system-facts":
            self._system_facts()
        elif self.command == "GET" and path == "/api/terminal":
            self._send_json(200, {"sessions": terminal_manager().list_active()})
        elif self.command == "POST" and path == "/api/terminal/start":
            self._terminal_start()
        elif self.command == "GET" and path == "/api/workspaces":
            self._workspaces_list()
        elif self.command == "POST" and path == "/api/workspaces":
            self._workspaces_add()
        elif self.command == "GET" and path == "/api/workspaces/context":
            self._workspaces_context()
        elif self.command == "GET" and path == "/api/workspaces/worktrees":
            self._workspaces_list_worktrees()
        elif self.command == "POST" and path == "/api/workspaces/worktrees":
            self._workspaces_create_worktree()
        elif self.command == "POST" and path == "/api/workspaces/worktrees/remove":
            self._workspaces_remove_worktree()
        elif self.command == "POST" and path == "/api/workspaces/worktrees/merge":
            self._workspaces_merge_worktree()
        elif self.command == "GET" and path == "/api/fs/browse":
            self._fs_browse()
        elif self.command == "POST" and path == "/api/fs/mkdir":
            self._fs_mkdir()
        elif self.command == "GET" and path == "/api/timeline":
            cat = parse_qs(urlparse(self.path).query).get("category", [None])[0]
            self._send_json(200, {"timeline": self.state.get_unified_timeline(category=cat)})
        elif path.startswith("/api/tasks/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3:
                task_id = parts[2]
                if self.command == "GET":
                    details = self.state.get_task_details(task_id)
                    if details:
                        self._send_json(200, details)
                    else:
                        self._send_json(404, {"error": "task_not_found"})
                elif self.command == "POST":
                    self._task_action(task_id)
            elif len(parts) == 4 and parts[3] == "stream" and self.command == "GET":
                self._task_stream(parts[2])
            elif len(parts) == 4 and parts[3] == "action" and self.command == "POST":
                self._task_action(parts[2])
            else:
                self._send_json(404, {"error": "not_found", "path": path})
        elif path.startswith("/api/terminal/") and self._terminal_sid() is not None:
            sid = self._terminal_sid()
            if self.command == "GET" and path.endswith("/drain"):
                self._terminal_drain(sid)
            elif self.command == "GET" and path.endswith("/replay"):
                self._terminal_replay(sid)
            elif self.command == "POST" and path.endswith("/input"):
                self._terminal_input(sid)
            elif self.command == "POST" and path.endswith("/resize"):
                self._terminal_resize(sid)
            elif self.command == "POST" and path.endswith("/kill"):
                self._terminal_kill(sid)
            else:
                self._send_json(404, {"error": "not_found", "path": path})
        else:
            self._send_json(404, {"error": "not_found", "path": path})

    # ------------------------------------------------------------------ #
    def _serve_index(self) -> None:
        index = _STATIC_DIR / "index.html"
        try:
            self._send(200, index.read_bytes(), "text/html; charset=utf-8")
        except OSError:
            self._send_json(500, {"error": "static_missing"})

    def _console(self) -> None:
        body = self._read_json_body()
        message = str(body.get("message") or "").strip()
        if not message:
            self._send_json(400, {"error": "message_required"})
            return
        created = self.state.create_task_from_message(
            message,
            priority=int(body.get("priority") or 85),
        )
        self.state.dispatch_in_background(max_spawn=1)
        self._send_json(200, {"accepted": True, **created})

    def _handle_v1_models(self) -> None:
        """GET /v1/models — OpenAI-compatible models list with case-insensitive HashSet deduplication.

        Eliminates duplicate bare models (such as 'gpt-5.6-luna' shared by a6api and codex)
        while preserving prefixed models ('a6api_...' and 'codex_...').
        """
        now = int(time.time())
        from hermes.platform.models.model_resolver import ModelResolver, deduplicate_models

        raw_models = [
            {"id": "deepseek-v4-flash", "root": "deepseek-v4-flash"},
            {"id": "a6api_deepseek-v4-flash", "root": "deepseek-v4-flash"},
            {"id": "gpt-5.6-luna", "root": "gpt-5.6-luna"},            # from a6api
            {"id": "gpt-5.6-luna", "root": "gpt-5.6-luna"},            # duplicate from codex (eliminated)
            {"id": "a6api_gpt-5.6-luna", "root": "gpt-5.6-luna"},      # preserved prefix
            {"id": "codex_gpt-5.6-luna", "root": "gpt-5.6-luna"},      # preserved prefix
            {"id": "claude-3-7-sonnet", "root": "claude-3-7-sonnet"},
        ]

        try:
            resolver = ModelResolver()
            for pid, profile in resolver._profiles.items():
                raw_models.append({"id": profile.id, "root": profile.id})
                for r in profile.routes:
                    raw_models.append({"id": r.provider_model_id, "root": r.provider_model_id})
                    raw_models.append({"id": f"{r.provider_id}_{r.provider_model_id}", "root": r.provider_model_id})
        except Exception:
            pass

        deduped = deduplicate_models(raw_models)
        models = [
            {
                "id": m["id"],
                "object": "model",
                "created": now,
                "owned_by": "haos",
                "permission": [],
                "root": m.get("root", m["id"]),
                "parent": None,
            }
            for m in deduped
        ]
        self._send_json(200, {"object": "list", "data": models})

    def _create_task(self) -> None:
        body = self._read_json_body()
        message = str(body.get("goal") or body.get("message") or "").strip()
        if not message:
            self._send_json(400, {"error": "goal_required"})
            return
        created = self.state.create_task_from_message(
            message,
            priority=int(body.get("priority") or 50),
            title=str(body.get("title") or "").strip() or None,
            requires_tasks=body.get("requires_tasks"),
        )
        self._send_json(200, {"accepted": True, **created})

    def _clear_tasks(self) -> None:
        body = self._read_json_body()
        reset_all = bool(body.get("all", False) or body.get("reset", False))
        if reset_all:
            count = self.state.reset_all_tasks()
        else:
            include_failed = bool(body.get("include_failed", False))
            count = self.state.clear_completed_tasks(include_failed=include_failed)
        self._send_json(200, {"success": True, "cleared": count, "reset_all": reset_all})

    def _dispatch(self) -> None:
        body = self._read_json_body()
        max_spawn = max(1, int(body.get("max_spawn") or 1))
        self.state.dispatch_in_background(max_spawn=max_spawn)
        self._send_json(200, {"accepted": True, "max_spawn": max_spawn})

    def _task_action(self, task_id: str) -> None:
        body = self._read_json_body()
        action = str(body.get("action") or "").strip().lower()
        if action in ("cancel", "stop", "kill"):
            ok = self.state.cancel_task(task_id, reason=str(body.get("reason") or "Cancelado pelo operador via UI"))
            self._send_json(200, {"success": ok, "action": action, "task_id": task_id})
        elif action in ("retry", "requeue", "ready"):
            ok = self.state.requeue_task(task_id)
            self._send_json(200, {"success": ok, "action": action, "task_id": task_id})
        elif action in ("delete", "remove", "clear"):
            ok = self.state.delete_task(task_id)
            self._send_json(200, {"success": ok, "action": action, "task_id": task_id})
        elif action in ("dispatch", "run"):
            self.state.dispatch_in_background(max_spawn=1)
            self._send_json(200, {"success": True, "action": action, "task_id": task_id})
        else:
            self._send_json(400, {"error": "invalid_action", "supported": ["cancel", "requeue", "dispatch", "delete"]})

    def _task_stream(self, task_id: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        task = self.state.kanban.get_task(task_id)
        if not task:
            msg = json.dumps({"error": "task_not_found", "task_id": task_id})
            try:
                self.wfile.write(f"event: error\ndata: {msg}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
            return

        ws_path = task.get("workspace_path")
        log_file = Path(ws_path) / ".haos" / "worker.log" if ws_path else None

        init_pkt = json.dumps({
            "task_id": task_id,
            "status": task.get("status"),
            "title": task.get("title"),
            "goal": (task.get("spec") or {}).get("goal") or task.get("title"),
            "elapsed_seconds": task.get("elapsed_seconds", 0),
            "tokens": task.get("tokens", 0),
            "cost": task.get("cost", 0.0),
        })
        try:
            self.wfile.write(f"event: status\ndata: {init_pkt}\n\n".encode("utf-8"))
            self.wfile.flush()
        except Exception:
            return

        last_pos = 0
        iterations = 0
        while iterations < 600:
            iterations += 1
            if not log_file:
                t_check = self.state.kanban.get_task(task_id)
                if t_check and t_check.get("workspace_path"):
                    ws_path = t_check.get("workspace_path")
                    log_file = Path(ws_path) / ".haos" / "worker.log"

            if log_file and log_file.is_file():
                try:
                    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_pos)
                        new_data = f.read()
                        if new_data:
                            last_pos = f.tell()
                            chunk = json.dumps({"text": new_data})
                            self.wfile.write(f"event: log\ndata: {chunk}\n\n".encode("utf-8"))
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                except Exception:
                    pass

            cur_task = self.state.kanban.get_task(task_id)
            if cur_task:
                cur_status = str(cur_task.get("status", "")).lower()
                status_pkt = json.dumps({
                    "task_id": task_id,
                    "status": cur_status,
                    "elapsed_seconds": cur_task.get("elapsed_seconds", 0),
                    "tokens": cur_task.get("tokens", 0),
                    "cost": cur_task.get("cost", 0.0),
                    "summary": (cur_task.get("result") or {}).get("summary", "") if hasattr(cur_task.get("result"), "get") else "",
                })
                try:
                    self.wfile.write(f"event: status\ndata: {status_pkt}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                except Exception:
                    pass

                if cur_status in ("done", "failed", "blocked", "completed"):
                    try:
                        self.wfile.write(b"event: done\ndata: {}\n\n")
                        self.wfile.flush()
                    except Exception:
                        pass
                    break

            try:
                time.sleep(1.0)
            except Exception:
                break

    def _intervene(self) -> None:
        body = self._read_json_body()
        target_id = str(body.get("target_id") or "").strip()
        action = str(body.get("action") or "").strip()
        reason = str(body.get("reason") or "Operator web intervention").strip()
        if not target_id or not action:
            self._send_json(400, {"error": "target_id_and_action_required"})
            return
        self.state.control_plane.record_intervention(target_id=target_id, action=action, reason=reason)
        self._send_json(200, {"status": "ok", "success": True, "target_id": target_id, "action": action})

    def _evolution_analyze(self) -> None:
        submitted = self.state.analyze_and_submit_proposals()
        self._send_json(200, {
            "submitted": len(submitted),
            "pending": len(self.state.ledger.pending()),
        })

    def _evolution_decide(self) -> None:
        body = self._read_json_body()
        proposal_id = str(body.get("proposal_id") or "").strip()
        verdict = str(body.get("verdict") or "").strip()
        approver = str(body.get("approver") or "").strip()
        if not proposal_id or verdict not in ("approved", "rejected"):
            self._send_json(400, {"error": "proposal_id_and_verdict_required"})
            return
        if not approver:
            self._send_json(400, {"error": "approver_required"})  # nunca inventa identidade
            return
        try:
            self.state.ledger.decide(
                proposal_id, verdict,
                approver=approver,
                rationale=str(body.get("rationale") or "").strip() or None,
            )
            self._send_json(200, {"ok": True, "proposal_id": proposal_id})
        except ValueError as exc:
            self._send_json(409, {"error": str(exc)})

    def _evolution_blast_radius(self) -> None:
        body = self._read_json_body()
        files = body.get("files") or []
        symbols = body.get("symbols") or []
        from hermes.platform.capabilities.lsp.unified_intelligence import CodeSymbolGraph, ImpactAnalyzer
        graph = CodeSymbolGraph()
        analyzer = ImpactAnalyzer(graph)
        try:
            blast = analyzer.calculate_blast_radius(
                modified_symbols=symbols,
                modified_files=files,
            )
            self._send_json(200, {
                "impacted_files": list(blast.affected_files),
                "impacted_callers": list(blast.affected_callers),
                "impacted_tests": list(blast.affected_test_suites),
                "risk_score": round(min(1.0, (len(blast.affected_files) * 0.15) + (len(blast.affected_callers) * 0.05)), 2),
                "severity": blast.severity,
            })
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _evolution_automerge(self) -> None:
        body = self._read_json_body()
        task_id = str(body.get("task_id") or "").strip()
        if not task_id:
            self._send_json(400, {"error": "task_id_required"})
            return
        from hermes.platform.workspaces.automerge import AutoMergeGate
        gate = AutoMergeGate()
        try:
            res = gate.evaluate_and_merge(task_id=task_id)
            self._send_json(200, res)
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _save_settings(self) -> None:
        body = self._read_json_body()
        saved = engine_settings.save_settings(self.state.data_dir, body)
        self.state.settings = saved
        engine_settings.apply_to_guard(self.state.guard, saved)
        self._send_json(200, {"ok": True, "settings": saved})

    def _reset_settings(self) -> None:
        defaults = engine_settings.reset_settings(self.state.data_dir)
        self.state.settings = defaults
        engine_settings.apply_to_guard(self.state.guard, defaults)
        self._send_json(200, {"ok": True, "settings": defaults})

    def _agent_settings(self) -> None:
        """Resumo read-only da configuração do agente (opcional, honesto).

        Usa o ``load_config`` do próprio fork (mesmo código do agente) para
        reportar as opções AGENT-level vigentes; alterações agent-level são
        feitas no dashboard oficial do agente — o standalone só lê.
        """
        try:
            from hermes_cli.config import load_config  # noqa: PLC0415
            cfg = load_config()
            summary = {
                "model": cfg.get("model", {}).get("model") or "",
                "provider": cfg.get("model", {}).get("provider") or "",
                "skin": (cfg.get("display") or {}).get("skin") or "default",
                "language": (cfg.get("display") or {}).get("language") or "en",
                "memory_enabled": bool((cfg.get("memory") or {}).get("memory_enabled", True)),
                "toolsets": (cfg.get("toolsets") or [])[:12],
                "note": "Editor completo do config.yaml real na aba 'Config "
                        "Agente' desta mesma UI standalone.",
            }
        except Exception as exc:  # noqa: BLE001
            summary = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        self._send_json(200, summary)

    # -- Agente: config completa (config.yaml real, editor v1-style) ------- #
    def _system_facts(self) -> None:
        from hermes.platform.webui import systemfacts  # noqa: PLC0415
        self._send_json(200, systemfacts.gather(self.state.data_dir))

    def _agent_config_read(self) -> None:
        from hermes.platform.webui import agentconfig  # noqa: PLC0415
        self._send_json(200, agentconfig.describe_config())

    def _agent_config_patch(self) -> None:
        from hermes.platform.webui import agentconfig  # noqa: PLC0415
        body = self._read_json_body()
        updates = body.get("updates")
        if not isinstance(updates, dict) or not updates:
            self._send_json(400, {"error": "updates_required"})
            return
        try:
            result = agentconfig.patch_config(
                updates,
                backup_dir=self.state.data_dir / "config-backups",
            )
        except agentconfig.ConfigUnavailable as exc:
            self._send_json(409, {"error": exc.code, "message": exc.message})
            return
        self._send_json(200, result)

    def _sessions_list(self) -> None:
        """List sessions from HAOS state.db for resume history."""
        import sqlite3
        import datetime
        raw_candidates = []
        if os.environ.get("HAOS_HOME"):
            raw_candidates.append(Path(os.environ["HAOS_HOME"]).expanduser() / "state.db")
        raw_candidates.append(Path("/run/media/adriano/e681b5ac-a4fb-44d4-aebf-9d6584065787/dsh-projetos/.haos/state.db"))
        raw_candidates.append(Path.home() / ".haos" / "state.db")
        raw_candidates.append(Path("/root/.haos/state.db"))

        # Deduplicate existing candidate dbs preserving order
        candidate_dbs = []
        seen_paths = set()
        for p in raw_candidates:
            resolved = str(p.resolve()) if p.exists() else str(p)
            if resolved not in seen_paths and p.exists():
                seen_paths.add(resolved)
                candidate_dbs.append(p)

        results = []
        seen_ids = set()
        for db_file in candidate_dbs:
            try:
                conn = sqlite3.connect(str(db_file), timeout=2.0)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, title, started_at, last_activity_at
                    FROM sessions
                    ORDER BY COALESCE(last_activity_at, started_at) DESC
                    LIMIT 30
                """)
                for r in cursor.fetchall():
                    sid = r["id"]
                    if sid in seen_ids:
                        continue
                    seen_ids.add(sid)
                    started_ts = r["started_at"] or 0
                    updated_ts = r["last_activity_at"] or started_ts
                    started_str = datetime.datetime.fromtimestamp(started_ts).strftime("%Y-%m-%d %H:%M:%S") if started_ts else ""
                    updated_str = datetime.datetime.fromtimestamp(updated_ts).strftime("%Y-%m-%d %H:%M:%S") if updated_ts else ""
                    
                    # Fetch first user message if title is missing
                    title = r["title"]
                    if not title or not str(title).strip():
                        msg_row = cursor.execute(
                            "SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1",
                            (sid,)
                        ).fetchone()
                        if msg_row and msg_row["content"]:
                            first_msg = str(msg_row["content"]).strip()
                            title = first_msg[:80] + ("…" if len(first_msg) > 80 else "")
                    
                    results.append({
                        "session_id": sid,
                        "title": title or f"Session {sid}",
                        "started_at": started_str,
                        "updated_at": updated_str,
                        "updated_ts": updated_ts,
                        "db": str(db_file),
                    })
                conn.close()
            except Exception as exc:
                logger.debug("Failed reading sessions from %s: %s", db_file, exc)
        
        results.sort(key=lambda x: x.get("updated_ts", 0), reverse=True)
        self._send_json(200, {"sessions": results[:40]})

    # -- Terminal interno (bridge PTY, estilo v1) --------------------------- #
    def _terminal_sid(self) -> Optional[str]:
        parts = urlparse(self.path).path.split("/")
        # /api/terminal/<sid>/<action>
        if len(parts) >= 4 and parts[:3] == ["", "api", "terminal"]:
            return parts[3]
        return None

    def _terminal_start(self) -> None:
        body = self._read_json_body()
        cwd = str(body.get("cwd") or "").strip() or None
        env = body.get("env")
        try:
            session = terminal_manager().start(cwd=cwd, env=env if isinstance(env, dict) else None)
            self._send_json(200, {
                "session_id": session.session_id,
                "shell": session.shell,
                "cwd": session.cwd,
            })
        except Exception as exc:
            self._send_json(400, {"error": str(exc), "wsl_recommended": True})

    def _terminal_input(self, sid: str) -> None:
        body = self._read_json_body()
        session = terminal_manager().get(sid)
        if session is None:
            self._send_json(404, {"error": "session_not_found"})
            return
        data = str(body.get("data") or "")
        ok = session.write_input(data)
        self._send_json(200, {"ok": ok, "running": session.proc.poll() is None})

    def _terminal_resize(self, sid: str) -> None:
        body = self._read_json_body()
        session = terminal_manager().get(sid)
        if session is None:
            self._send_json(404, {"error": "session_not_found"})
            return
        session.resize(int(body.get("rows") or 24), int(body.get("cols") or 80))
        self._send_json(200, {"ok": True})

    def _terminal_drain(self, sid: str) -> None:
        session = terminal_manager().get(sid)
        if session is None:
            self._send_json(404, {"error": "session_not_found"})
            return
        self._send_json(200, session.drain())

    def _terminal_replay(self, sid: str) -> None:
        session = terminal_manager().get(sid)
        if session is None:
            self._send_json(404, {"error": "session_not_found"})
            return
        self._send_json(200, session.get_replay())

    def _terminal_kill(self, sid: str) -> None:
        removed = terminal_manager().remove(sid)
        if not removed:
            self._send_json(404, {"error": "session_not_found"})
            return
        self._send_json(200, {"ok": True})

    # -- Workspaces & File Explorer API (DSH Style) ----------------------- #
    def _workspaces_file(self) -> Path:
        data_dir = getattr(self.state, "data_dir", Path.home() / ".haos")
        return Path(data_dir) / "workspaces.json"

    def _workspaces_list(self) -> None:
        ws_file = self._workspaces_file()
        default_ws = {
            "id": "default",
            "name": "Default (HERMES-TURBO)",
            "path": os.getcwd(),
            "created_at": 1788800000,
        }
        items = [default_ws]
        if ws_file.exists():
            try:
                data = json.loads(ws_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    # deduplicate by path/id
                    seen_paths = {default_ws["path"]}
                    for w in data:
                        if isinstance(w, dict) and w.get("path") and w["path"] not in seen_paths:
                            seen_paths.add(w["path"])
                            items.append(w)
            except Exception as exc:
                logger.debug("Failed reading workspaces.json: %s", exc)
        self._send_json(200, {"workspaces": items})

    def _workspaces_add(self) -> None:
        body = self._read_json_body()
        target_path = str(body.get("path") or "").strip()
        name = str(body.get("name") or "").strip()
        if not target_path:
            self._send_json(400, {"error": "path_required"})
            return
        p = Path(target_path).expanduser().resolve()
        if not p.exists() or not p.is_dir():
            self._send_json(400, {"error": "path_not_found_or_not_dir"})
            return
        if not name:
            name = p.name or str(p)
        ws_id = f"ws_{p.name.lower().replace(' ', '_')}_{abs(hash(str(p))) % 10000}"
        ws_item = {
            "id": ws_id,
            "name": name,
            "path": str(p),
            "created_at": time.time(),
        }
        ws_file = self._workspaces_file()
        current = []
        if ws_file.exists():
            try:
                current = json.loads(ws_file.read_text(encoding="utf-8"))
                if not isinstance(current, list):
                    current = []
            except Exception:
                current = []
        current = [w for w in current if isinstance(w, dict) and w.get("path") != str(p)]
        current.append(ws_item)
        ws_file.parent.mkdir(parents=True, exist_ok=True)
        ws_file.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        self._send_json(200, ws_item)

    def _workspaces_context(self) -> None:
        query_str = urlparse(self.path).query
        params = parse_qs(query_str)
        ws_path = params.get("path", [None])[0]
        if not ws_path:
            p = Path.cwd()
        else:
            p = Path(ws_path).expanduser().resolve()

        haos_dir = p / ".haos"
        memories_dir = haos_dir / "memories"
        vault_dir = p / "vault"
        graphrag_dir = haos_dir / "graphrag"

        memories = []
        if memories_dir.is_dir():
            for f in memories_dir.glob("*.md"):
                memories.append(f.name)

        adrs = []
        if vault_dir.is_dir():
            for f in vault_dir.glob("*.md"):
                adrs.append(f.name)

        is_git = (p / ".git").exists()

        self._send_json(200, {
            "workspace_path": str(p),
            "is_git": is_git,
            "has_haos_dir": haos_dir.is_dir(),
            "memories": memories,
            "adrs": adrs,
            "has_graphrag": graphrag_dir.is_dir(),
        })

    def _workspaces_list_worktrees(self) -> None:
        query_str = urlparse(self.path).query
        params = parse_qs(query_str)
        repo_root = params.get("repo_root", [None])[0] or os.getcwd()
        from hermes.platform.workspaces.git_worktree import GitWorktreeManager
        mgr = GitWorktreeManager(repo_root=Path(repo_root))
        try:
            wts = mgr.list_worktrees()
            self._send_json(200, {"worktrees": wts, "count": len(wts)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _workspaces_create_worktree(self) -> None:
        body = self._read_json_body()
        task_id = str(body.get("task_id") or f"t_{uuid.uuid4().hex[:6]}")
        repo_root = body.get("repo_root") or os.getcwd()
        base_branch = body.get("base_branch")
        from hermes.platform.workspaces.git_worktree import GitWorktreeManager
        mgr = GitWorktreeManager(repo_root=Path(repo_root))
        try:
            wt_path = mgr.create_worktree(task_id=task_id, base_branch=base_branch)
            self._send_json(200, {
                "success": True,
                "task_id": task_id,
                "worktree_path": str(wt_path),
                "branch": f"haos/task-{task_id}",
            })
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _workspaces_remove_worktree(self) -> None:
        body = self._read_json_body()
        task_id = str(body.get("task_id") or "").strip()
        repo_root = body.get("repo_root") or os.getcwd()
        force = bool(body.get("force", True))
        delete_branch = bool(body.get("delete_branch", False))
        if not task_id:
            self._send_json(400, {"error": "task_id_required"})
            return
        from hermes.platform.workspaces.git_worktree import GitWorktreeManager
        mgr = GitWorktreeManager(repo_root=Path(repo_root))
        try:
            removed = mgr.remove_worktree(task_id=task_id, force=force, delete_branch=delete_branch)
            self._send_json(200, {"success": True, "task_id": task_id, "removed": removed})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _workspaces_merge_worktree(self) -> None:
        body = self._read_json_body()
        task_id = str(body.get("task_id") or "").strip()
        repo_root = body.get("repo_root") or os.getcwd()
        target_branch = body.get("target_branch")
        squash = bool(body.get("squash", False))
        if not task_id:
            self._send_json(400, {"error": "task_id_required"})
            return
        from hermes.platform.workspaces.git_worktree import GitWorktreeManager
        mgr = GitWorktreeManager(repo_root=Path(repo_root))
        try:
            res = mgr.merge_worktree(task_id=task_id, target_branch=target_branch, squash=squash)
            self._send_json(200, res)
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _fs_browse(self) -> None:
        query_str = urlparse(self.path).query
        params = parse_qs(query_str)
        raw_path = params.get("path", [None])[0]
        show_hidden = params.get("show_hidden", ["false"])[0].lower() in ("1", "true", "yes")

        if not raw_path or not str(raw_path).strip():
            target_dir = Path.home()
        else:
            target_dir = Path(raw_path).expanduser().resolve()

        if not target_dir.exists():
            target_dir = Path.home()
        if not target_dir.is_dir():
            target_dir = target_dir.parent

        items = []
        try:
            with os.scandir(target_dir) as it:
                for entry in it:
                    if not show_hidden and entry.name.startswith("."):
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=True)
                        items.append({
                            "name": entry.name,
                            "path": str(Path(entry.path).resolve()),
                            "is_dir": is_dir,
                        })
                    except OSError:
                        continue
        except PermissionError:
            self._send_json(403, {"error": "permission_denied", "path": str(target_dir)})
            return
        except Exception as exc:
            self._send_json(500, {"error": str(exc), "path": str(target_dir)})
            return

        # Sort: directories first, then alphabetically
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        self._send_json(200, {
            "current_path": str(target_dir),
            "parent_path": str(target_dir.parent) if target_dir.parent != target_dir else None,
            "home_path": str(Path.home()),
            "items": items,
        })

    def _fs_mkdir(self) -> None:
        body = self._read_json_body()
        base_dir = str(body.get("base_dir") or "").strip()
        folder_name = str(body.get("name") or "").strip()
        if not base_dir or not folder_name:
            self._send_json(400, {"error": "base_dir_and_name_required"})
            return
        if "/" in folder_name or "\\" in folder_name or folder_name in (".", ".."):
            self._send_json(400, {"error": "invalid_folder_name"})
            return
        target = (Path(base_dir).expanduser() / folder_name).resolve()
        try:
            target.mkdir(parents=False, exist_ok=False)
            self._send_json(200, {"ok": True, "created_path": str(target)})
        except FileExistsError:
            self._send_json(409, {"error": "already_exists"})
        except PermissionError:
            self._send_json(403, {"error": "permission_denied"})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


_TERMINAL_MANAGER = None
_TERMINAL_MANAGER_LOCK = threading.Lock()


def terminal_manager():
    """Singleton do TerminalManager (uma instância por processo servidor)."""
    global _TERMINAL_MANAGER
    if _TERMINAL_MANAGER is None:
        with _TERMINAL_MANAGER_LOCK:
            if _TERMINAL_MANAGER is None:
                from hermes.platform.webui.terminal import TerminalManager  # noqa: PLC0415
                _TERMINAL_MANAGER = TerminalManager()
    return _TERMINAL_MANAGER


def make_standalone_server(
    data_dir: Optional[Path] = None,
    *,
    host: str = "0.0.0.0",
    port: int = _DEFAULT_PORT,
    title: str = "HAOS Standalone",
) -> tuple[ThreadingHTTPServer, HAOSStandaloneState, str]:
    """Constrói o servidor standalone. Retorna (server, state, base_url)."""
    if data_dir is not None:
        data_dir = Path(data_dir)
    else:
        data_dir = Path(os.environ.get("HAOS_DATA_DIR") or os.environ.get("HAOS_HOME", Path.home() / ".haos"))
    state = HAOSStandaloneState(data_dir)

    def handler_factory(*args, **kwargs):
        return HAOSStandaloneHandler(*args, state=state, title=title, **kwargs)

    server = HAOSThreadingHTTPServer((host, port), handler_factory)
    base = f"http://{host}:{server.server_address[1]}"

    # Background WAL auto-checkpoint: prevents unbounded WAL accumulation across long runs
    def _wal_auto_maintenance():
        while True:
            time.sleep(300)  # Checkpoint every 5 minutes
            try:
                candidate_dbs = [
                    state.kanban_db,
                    state.events_db,
                    state.data_dir / "state.db",
                    state.data_dir / "memory" / "reconciled_memories.db",
                    state.data_dir / "memory" / "ragflow.db",
                    Path.home() / ".hermes" / "state.db",
                    Path.home() / ".hermes" / "kanban.db",
                ]
                for db in candidate_dbs:
                    if db and Path(db).exists():
                        try:
                            with sqlite3.connect(str(db), timeout=5.0) as conn:
                                conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
                        except Exception:
                            pass
            except Exception:
                pass

    threading.Thread(target=_wal_auto_maintenance, daemon=True, name="haos-wal-checkpoint").start()

    return server, state, base


def main() -> None:
    """CLI entrypoint para executar o HAOS Standalone WebUI."""
    import argparse
    parser = argparse.ArgumentParser(description="HAOS Standalone WebUI Server")
    parser.add_argument("--host", default=os.environ.get("HAOS_HOST", "0.0.0.0"), help="Host para escutar")
    parser.add_argument("--port", type=int, default=int(os.environ.get("HAOS_PORT", _DEFAULT_PORT)), help="Porta HTTP")
    parser.add_argument("--data-dir", default=None, help="Diretório persistente do engine (default: ~/.haos)")
    args = parser.parse_args()

    server, state, base_url = make_standalone_server(
        data_dir=Path(args.data_dir) if args.data_dir else None,
        host=args.host,
        port=args.port,
    )
    print("=" * 70)
    print("🚀 STARTING HAOS STANDALONE CONTROL PLANE")
    print(f"[*] Bind Host: {args.host}")
    print(f"[*] Port:      {args.port}")
    print(f"[*] Data Dir:  {state.data_dir}")
    print(f"[*] URL:       {base_url}")
    print("=" * 70)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping HAOS server...")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
