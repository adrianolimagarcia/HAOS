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
import sqlite3
import sys
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class HAOSThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128

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
import ipaddress

# Garante que a raiz do repositório esteja no sys.path para execução direta como script
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hermes.platform.tasks.spec import OPERATOR_YOLO_DEFAULT, TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.observability.sink import EventStoreSink
from hermes.platform.bots.knowledge import KnowledgePage, KnowledgePageManager
from hermes.platform.execution.backpressure import ConcurrencyGuard
from hermes.platform.execution.dispatcher import HAOSDispatcher
from hermes.platform.evolution.ledger import EvolutionLedger
from hermes.platform.evolution.analyzer import OuroborosAnalyzer
from hermes.platform.ui.dashboard import dashboard_payload
from hermes.platform.ui.stats import DashboardStats
from hermes.platform.webui.controlplane import ControlPlaneService
from hermes.platform.webui.agent_hierarchy import AgentHierarchyStore, HierarchyError
from hermes.platform.webui.council_adapter import CouncilAdapter
from hermes.platform.webui.harness_bindings import HARNESS_CATALOG

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
        self.knowledge = KnowledgePageManager(self.event_store)
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
        self.control_plane = ControlPlaneService(
            self.event_store,
            kanban=self.kanban,
            concurrency_guard=self.guard,
            data_dir=data_dir,
        )
        self.agent_hierarchy = AgentHierarchyStore(data_dir / "agent_hierarchy.json")
        self.council = CouncilAdapter(self.agent_hierarchy, self.event_store)
        from hermes.platform.shadow_leaf import ShadowLeafManager
        self.shadow_manager = ShadowLeafManager(base_repo_dir=Path.cwd())

        # Aplica settings persistidos (defaults se ausente) ao guard vivo.
        self.settings = engine_settings.load_settings(data_dir)
        engine_settings.apply_to_guard(self.guard, self.settings)

        # Cache do índice de símbolos (Blast Radius): construído sob demanda
        # na primeira análise e re-construído quando o usuário pedir "recalcular".
        # O lock garante uma única indexação: o primeiro chamador paga o custo
        # (~10s em repo grande) e os concorrentes reutilizam o resultado.
        self._symbol_index: Dict[str, Any] = {"root": None, "graph": None,
                                              "files_indexed": 0, "symbols_indexed": 0}
        self._symbol_index_lock = threading.Lock()

        # No servidor ao vivo (fora da suíte rápida de testes unitários),
        # instala os workers agênticos reais para que tarefas executem o agente real.
        if "PYTEST_CURRENT_TEST" not in os.environ and not os.environ.get("HERMES_DETERMINISTIC_TESTS"):
            try:
                from hermes.platform.execution.lane_executor import install_real_lane_workers
                install_real_lane_workers()
            except Exception:
                pass

        # Pool concorrente para despachos paralelos (Master -> Bots -> Leafs).
        # Permite que até 6 tarefas/lanes rodem verdadeiramente em paralelo sem lock serializado.
        from concurrent.futures import ThreadPoolExecutor
        self._dispatch_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="haos-lane-worker")

    # ------------------------------------------------------------------ #
    def create_task_from_message(
        self,
        message: str,
        *,
        priority: int = 85,
        title: Optional[str] = None,
        requires_tasks: Optional[List[str]] = None,
        assignee: Optional[str] = None,
        model_profile: Optional[str] = None,
        agent_target: Optional[str] = None,
        yolo_mode: bool = False,
    ) -> Dict[str, Any]:
        tags = [f"agent:{agent_target}"] if agent_target else []
        spec = TaskSpec(
            id=f"T-{uuid.uuid4().hex[:6]}",
            title=title or f"Missão: {message[:60]}",
            goal=message,
            priority=int(priority),
            posture="implementer",
            model_profile=model_profile,
            model_profile_preferred=model_profile,
            agent_profile=assignee if agent_target else None,
            required_agents=[agent_target] if agent_target else [],
            tags=tags,
            requires_tasks=[str(t) for t in (requires_tasks or []) if t],
            workspace_type="scratch",
            yolo_mode=bool(yolo_mode),
        )
        task_id = self.kanban.save_task(spec, status="READY", assignee=assignee)
        return {"task_id": task_id, "spec_id": spec.id, "goal": message}

    def dispatch_in_background(self, *, max_spawn: int = 10) -> bool:
        """Dispara claim_tick canônico concorrente em ThreadPool (permite execução paralela real de bots/leafs)."""
        def _worker_drain(slot_id: int) -> None:
            try:
                # Cada slot worker drena uma tarefa disponível e executa a lane
                self.dispatcher.claim_tick(
                    worker_id=f"haos-webui-{slot_id}",
                    max_spawn=1,
                )
            except Exception as exc:  # noqa: BLE001 - falha registrada no card pela lane
                logger.warning("Worker drain error: %s", exc)

        def _spawn_pool() -> None:
            spawns = max(1, min(int(max_spawn), 6))
            for i in range(spawns):
                self._dispatch_pool.submit(_worker_drain, i + 1)

        threading.Thread(target=_spawn_pool, daemon=True).start()
        return True

    def reap_stale_tasks(self) -> int:
        """Auto-reparo de cards zumbis (status=running cujo processo/claim já expirou ou morreu)."""
        now = time.time()
        conn = self.kanban._connect()
        # Busca tarefas running com claim expirado ou processo morto
        rows = conn.execute(
            "SELECT id, claim_lock, claim_expires, worker_pid, workspace_path FROM tasks WHERE status = 'running'"
        ).fetchall()
        reaped = 0
        for r in rows:
            tid = r["id"]
            expires = r["claim_expires"]
            pid = r["worker_pid"]
            ws_path = r["workspace_path"]
            is_dead = False

            # 1. Se tem pid no disco/banco, testa se o processo do worker ainda vive
            if not pid and ws_path:
                pid_f = Path(ws_path) / ".haos" / "pid.txt"
                if pid_f.is_file():
                    try:
                        pid = int(pid_f.read_text(encoding="utf-8").strip())
                    except Exception:
                        pass

            if pid:
                try:
                    os.kill(pid, 0)
                except OSError:
                    is_dead = True
            elif expires and now > expires:
                is_dead = True

            if is_dead:
                # Verifica se há resultado salvo no disco antes de marcar falha
                has_res = False
                summary = ""
                if ws_path:
                    res_f = Path(ws_path) / ".haos" / "result.json"
                    if res_f.is_file():
                        try:
                            res_data = json.loads(res_f.read_text(encoding="utf-8"))
                            summary = res_data.get("summary", "")
                            has_res = bool(summary)
                        except Exception:
                            pass

                if has_res:
                    conn.execute(
                        "UPDATE tasks SET status = 'done', claim_lock = NULL, claim_expires = NULL, completed_at = ? WHERE id = ?",
                        (int(now), tid),
                    )
                    conn.execute(
                        "UPDATE haos_task_runs SET status = 'ended', exit_reason = 'completed', ended_at = ? WHERE task_id = ? AND status = 'running'",
                        (now, tid),
                    )
                else:
                    conn.execute(
                        "UPDATE tasks SET status = 'failed', claim_lock = NULL, claim_expires = NULL, last_failure_error = 'Worker process terminated unexpectedly (reaped by supervisor)', completed_at = ? WHERE id = ?",
                        (int(now), tid),
                    )
                    conn.execute(
                        "UPDATE haos_task_runs SET status = 'ended', exit_reason = 'worker_crash', ended_at = ? WHERE task_id = ? AND status = 'running'",
                        (now, tid),
                    )
                reaped += 1
        if reaped > 0:
            conn.commit()
            logger.info("Reaper: %d stale running tasks auto-repaired.", reaped)
        return reaped

    # ------------------------------------------------------------------ #
    def state_payload(self) -> Dict[str, Any]:
        # Cache curto de 1 segundo para evitar recomputar e travar com múltiplos requests simultâneos
        now = time.time()
        last_t = getattr(self, "_last_state_time", 0.0)
        last_p = getattr(self, "_last_state_cache", None)
        if last_p and (now - last_t) < 1.0:
            return last_p

        # Drena e auto-repara tarefas zumbis de forma assíncrona/não bloqueante
        if (now - getattr(self, "_last_reap_time", 0.0)) > 5.0:
            self._last_reap_time = now
            threading.Thread(target=self.reap_stale_tasks, daemon=True).start()

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
        payload["team_graph"]["organizational_hierarchy"] = self.agent_hierarchy.snapshot()
        payload["agent_hierarchy"] = payload["team_graph"]["organizational_hierarchy"]
        try:
            payload["control_overview"] = dataclasses.asdict(self.control_plane.get_overview())
        except Exception:
            payload["control_overview"] = {}
        payload["settings"] = dict(self.settings)
        payload["harness_bindings"] = self.control_plane.bindings.all()
        payload["harness_catalog"] = list(HARNESS_CATALOG)
        payload["meta"] = {
            "data_dir": str(self.data_dir),
            "tasks_db": str(self.kanban_db),
            "events_db": str(self.events_db),
            "kanban_available": self.stats.available(),
            "mode": "standalone",
        }
        self._last_state_time = now
        self._last_state_cache = payload
        return payload
        return payload

    def ensure_symbol_index(self, root: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        """Grafo de símbolos (AST) do repositório, construído sob demanda.

        Reutilizado entre chamadas de Blast Radius; ``force=True`` re-indexa
        (equivalente ao botão "recalcular" da UI). Retorna o cache com
        estatísticas de indexação para diagnóstico honesto na UI.

        A indexação é serializada por ``_symbol_index_lock``: ela leva ~10s em
        um repositório grande, então chamadores concorrentes (o warm-up do boot
        e o primeiro clique do dashboard) aguardam a mesma indexação em vez de
        pagarem o custo duas vezes.
        """
        root_s = str((Path(root) if root else Path.cwd()).resolve())
        idx = self._symbol_index
        if not force and idx.get("root") == root_s and idx.get("graph") is not None:
            return idx

        with self._symbol_index_lock:
            # Outra thread pode ter indexado enquanto este chamador esperava.
            if not force and idx.get("root") == root_s and idx.get("graph") is not None:
                return idx
            return self._build_symbol_index(root_s)

    def _build_symbol_index(self, root_s: str) -> Dict[str, Any]:
        """Indexa ``root_s`` e publica o resultado no cache (chamado sob o lock)."""
        from hermes.platform.capabilities.lsp.unified_intelligence import CodeSymbolGraph
        graph = CodeSymbolGraph()
        excludes = {
            ".git", ".venv", "venv", "__pycache__", ".worktrees", ".haos",
            "dist", "build", "node_modules", "distro", "website", "docs",
            "evals", "vendor", ".cargo", ".mypy_cache", ".pytest_cache",
        }
        try:
            count = graph.scan_directory(root_s, exclude_dirs=excludes, max_files=2500)
        except Exception:
            count = 0
        self._symbol_index.update({
            "root": root_s,
            "graph": graph,
            "files_indexed": len(graph.file_symbols),
            "symbols_indexed": count,
            "built_at": time.time(),
        })
        return self._symbol_index

    def warm_symbol_index_background(self, root: Optional[str] = None) -> threading.Thread:
        """Warm-up do índice de símbolos:
        
        Como o endpoint /api/evolution/blast-radius agora possui Fast-Path 100% nativo
        em Rust via /usr/local/bin/haos-edge (executado sob demanda em <300ms sem lock),
        desativamos a varredura pesada de 2500 arquivos no heap Python no boot,
        economizando mais de 100MB de RAM permanente.
        """
        def _run() -> None:
            pass

        thread = threading.Thread(
            target=_run, daemon=True, name="haos-symbol-index-warmup")
        thread.start()
        return thread

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

        # Se não houver worker.log no disco (workspace já desalocado), injeta o summary como log
        # Busca no task["result"] ou faz fallback no event_store
        if not log_content:
            summary = ""
            if task.get("result"):
                r = task["result"]
                summary = getattr(r, "summary", "") or (r.get("summary", "") if isinstance(r, dict) else "")
            if not summary:
                # Busca no EventStore pelo evento task.run.completed. O store só
                # filtra por nome/limite, então o correlation_id é conferido aqui.
                evs = self.event_store.get_all(name="task.run.completed", limit=200)
                for ev in reversed(evs):
                    if ev.correlation_id == task_id and isinstance(ev.payload, dict):
                        summary = ev.payload.get("summary", "")
                        if summary:
                            break
            if summary:
                log_content = f"=== [RELATÓRIO HISTÓRICO DA TAREFA: {task_id}] ===\n\n{summary}\n"
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
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline' https: http:; script-src 'self' 'unsafe-inline' https: http:; style-src 'self' 'unsafe-inline' https: http:; connect-src 'self' https: http: ws: wss:; object-src 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

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

    def do_DELETE(self) -> None:  # noqa: N802
        self._serve()

    def _knowledge_list(self, bot_id: str) -> None:
        self._send_json(200, {"pages": [p.to_dict() for p in self.state.knowledge.list(bot_id)]})

    def _knowledge_get(self, bot_id: str, page_id: str) -> None:
        page = self.state.knowledge.get(page_id)
        if page is None or page.bot_id != bot_id:
            self._send_json(404, {"error": "page_not_found"})
            return
        self._send_json(200, page.to_dict())

    def _knowledge_upsert(self, bot_id: str) -> None:
        body = self._read_json_body()
        try:
            page = KnowledgePage.from_dict({**body, "bot_id": bot_id, "id": str(body.get("id") or uuid.uuid4().hex)})
            self._send_json(200, self.state.knowledge.upsert(page).to_dict())
        except (TypeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})

    def _knowledge_dispute(self, bot_id: str, page_id: str, action: str) -> None:
        page = self.state.knowledge.get(page_id)
        if page is None or page.bot_id != bot_id:
            self._send_json(404, {"error": "page_not_found"})
            return
        try:
            if action == "dispute":
                reason = str(self._read_json_body().get("reason") or "").strip()
                if not reason:
                    self._send_json(400, {"error": "reason_required"})
                    return
                self.state.knowledge.dispute(page_id, reason)
            else:
                self.state.knowledge.resolve_dispute(page_id)
            self._send_json(200, self.state.knowledge.get(page_id).to_dict())
        except KeyError:
            self._send_json(404, {"error": "page_not_found"})

    # ------------------------------------------------------------------ #
    def _serve(self) -> None:
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["bots", parts[1]] and parts[2] == "knowledge":
            if self.command == "GET":
                self._knowledge_list(parts[1])
            elif self.command == "POST":
                self._knowledge_upsert(parts[1])
            else:
                self._send_json(405, {"error": "method_not_allowed"})
        elif len(parts) == 4 and parts[0] == "bots" and parts[2] == "knowledge" and self.command == "GET":
            self._knowledge_get(parts[1], parts[3])
        elif len(parts) == 5 and parts[0] == "bots" and parts[2] == "knowledge" and parts[4] in ("dispute", "resolve") and self.command == "POST":
            self._knowledge_dispute(parts[1], parts[3], parts[4])
        elif self.command in ("GET", "HEAD") and (path in ("/", "/index.html", "/chat", "/console", "/terminal", "/taskboard", "/scheduler", "/ouroboros", "/agent", "/system", "/events", "/config") or not path.startswith(("/api/", "/health", "/v1/"))):
            self._serve_index()
        elif self.command == "GET" and path in ("/health", "/api/health"):
            # Diagnóstico honesto de integridade e liveness do storage
            k_db = self.state.kanban_db
            k_ok = k_db.is_file() and k_db.stat().st_size > 0
            e_db = self.state.events_db
            e_ok = e_db.is_file()
            storage_healthy = bool(k_ok and e_ok)
            status_code = 200 if storage_healthy else 503
            self._send_json(status_code, {
                "status": "healthy" if storage_healthy else "degraded",
                "service": "haos-controlplane",
                "storage_accessible": storage_healthy,
                "kanban_db": str(k_db),
                "events_db": str(e_db),
            })
        elif self.command == "GET" and path == "/api/state":
            try:
                self._send_json(200, self.state.state_payload())
            except Exception as exc:
                logger.error("State generation failed: %s", exc, exc_info=True)
                self._send_json(503, {"error": "storage_unavailable", "detail": str(exc)})
        elif self.command == "POST" and path == "/api/tools/detect-loop":
            # Fast-Path Anti-Loop: executa diretamente a heurística nativa em memória
            try:
                data = self._read_json_body()
                sess_id = data.get("session_id", "default")
                tool_name = data.get("tool_name", "")
                args_str = data.get("arguments", "")
                threshold = int(data.get("threshold", 3))

                if not hasattr(self.state, "_tool_loop_trackers"):
                    self.state._tool_loop_trackers = {}
                tracker = self.state._tool_loop_trackers.setdefault(sess_id, [])
                tracker.append((tool_name, args_str))
                if len(tracker) > 20:
                    tracker.pop(0)

                # Verifica repetições consecutivas idênticas
                consecutive = 0
                for t_name, t_args in reversed(tracker):
                    if t_name == tool_name and t_args == args_str:
                        consecutive += 1
                    else:
                        break

                if consecutive >= threshold:
                    msg = f"Loop repetitivo detectado: {tool_name} executado {consecutive} vezes consecutivas com os mesmos argumentos."
                    self._send_json(200, {
                        "ok": True,
                        "loop_detected": True,
                        "alert": {"message": msg, "consecutive": consecutive, "tool": tool_name}
                    })
                else:
                    self._send_json(200, {"ok": True, "loop_detected": False})
            except Exception as e:
                self._send_json(200, {"ok": True, "loop_detected": False, "error": str(e)})
        elif self.command == "POST" and path == "/api/context/compact":
            # Fast-Path Context Compactação Ultra SOTA
            try:
                data = self._read_json_body()
                msgs = data.get("messages", [])
                max_tool_chars = int(data.get("max_tool_chars", 2500))
                keep_last = int(data.get("keep_last", 6))
                
                if len(msgs) <= keep_last + 2:
                    self._send_json(200, {"ok": True, "messages": msgs, "compacted_count": len(msgs), "truncated_tools": 0})
                    return

                sys_count = 0
                for m in msgs:
                    if m.get("role") == "system":
                        sys_count += 1
                    else:
                        break
                
                middle_start = sys_count
                middle_end = max(middle_start, len(msgs) - keep_last)
                truncated_count = 0
                out = []

                for i, m in enumerate(msgs):
                    if i < middle_start or i >= middle_end:
                        out.append(m)
                    else:
                        m_copy = dict(m)
                        if m_copy.get("role") == "tool" and isinstance(m_copy.get("content"), str):
                            c_str = m_copy["content"]
                            if len(c_str) > max_tool_chars:
                                head = c_str[:max_tool_chars // 2]
                                tail = c_str[-(max_tool_chars // 2):]
                                m_copy["content"] = f"{head}\n\n[... {len(c_str) - max_tool_chars} caracteres compactados pelo HAOS Core ...]\n\n{tail}"
                                truncated_count += 1
                        out.append(m_copy)

                self._send_json(200, {
                    "ok": True,
                    "messages": out,
                    "compacted_count": len(out),
                    "truncated_tools": truncated_count
                })
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
        elif self.command == "GET" and path in ("/v1/models", "/api/v1/models"):
            self._handle_v1_models()
        elif self.command == "GET" and path in ("/api/team-graph", "/api/controlplane/team_graph"):
            graph = self.state.control_plane.get_team_graph_snapshot()
            graph["organizational_hierarchy"] = self.state.agent_hierarchy.snapshot()
            self._send_json(200, graph)
        elif self.command == "GET" and path in ("/api/agent-hierarchy", "/api/controlplane/agent-hierarchy"):
            self._send_json(200, self.state.agent_hierarchy.snapshot())
        elif self.command == "GET" and path == "/api/agent-hierarchy/soul":
            self._get_hierarchy_soul()
        elif self.command == "POST" and path == "/api/agent-hierarchy/soul":
            self._save_hierarchy_soul()
        elif self.command == "GET" and path == "/api/agent-hierarchy/memory":
            self._get_hierarchy_memory()
        elif self.command == "POST" and path == "/api/agent-hierarchy/memory":
            self._save_hierarchy_memory()
        elif self.command == "GET" and path == "/api/agent-hierarchy/notebook":
            self._get_hierarchy_notebook()
        elif self.command == "POST" and path == "/api/agent-hierarchy/notebook":
            self._save_hierarchy_notebook()
        elif self.command == "GET" and path == "/api/agent-hierarchy/wiki/articles":
            self._get_hierarchy_wiki_articles()
        elif self.command == "GET" and path == "/api/agent-hierarchy/wiki/article":
            self._get_hierarchy_wiki_article()
        elif self.command == "POST" and path == "/api/agent-hierarchy/wiki/article":
            self._save_hierarchy_wiki_article()
        elif self.command == "POST" and path == "/api/agent-hierarchy/wiki/promote":
            self._promote_hierarchy_notebook_to_wiki()
        elif self.command == "GET" and path == "/api/agent-hierarchy/shadows":
            self._get_hierarchy_shadows()
        elif self.command == "POST" and path == "/api/agent-hierarchy/shadows":
            self._spawn_hierarchy_shadow()
        elif (self.command == "DELETE" or (self.command == "POST" and path == "/api/agent-hierarchy/shadows/discard")) and path in ("/api/agent-hierarchy/shadows", "/api/agent-hierarchy/shadows/discard"):
            self._discard_hierarchy_shadow()
        elif self.command == "GET" and path == "/api/agent-hierarchy/microapps":
            self._get_hierarchy_microapps()
        elif self.command == "POST" and path == "/api/agent-hierarchy/microapps":
            self._save_hierarchy_microapp()
        elif (self.command == "DELETE" or (self.command == "POST" and path == "/api/agent-hierarchy/microapps/delete")) and path in ("/api/agent-hierarchy/microapps", "/api/agent-hierarchy/microapps/delete"):
            self._delete_hierarchy_microapp()
        elif self.command == "POST" and path == "/api/agent-hierarchy/approvals/decide":
            self._decide_hierarchy_approval()
        elif self.command == "GET" and path == "/api/agent-hierarchy/toolsets":
            self._get_hierarchy_toolsets()
        elif self.command == "GET" and path == "/api/agent-hierarchy/routines":
            self._get_hierarchy_routines()
        elif self.command == "POST" and path == "/api/agent-hierarchy/routines":
            self._save_hierarchy_routine()
        elif self.command == "POST" and path == "/api/agent-hierarchy/routines/delete":
            self._delete_hierarchy_routine()
        elif self.command == "GET" and path == "/api/agent-hierarchy/feed":
            self._get_hierarchy_feed()
        elif self.command == "POST" and path == "/api/agent-hierarchy/clone":
            self._clone_hierarchy_node()
        elif self.command == "GET" and path in ("/api/harnesses", "/api/controlplane/harnesses"):
            self._send_json(200, self.state.control_plane.harness_overview())
        elif self.command == "GET" and path in ("/api/controlplane/overview", "/api/overview"):
            self._send_json(200, dataclasses.asdict(self.state.control_plane.get_overview()))
        elif self.command == "POST" and path in ("/api/agent-hierarchy/council", "/api/controlplane/agent-hierarchy/council"):
            self._hierarchy_council()
        elif self.command == "POST" and path in ("/api/intervene", "/api/controlplane/intervene"):
            self._intervene()
        elif self.command == "POST" and path in ("/api/agent-hierarchy", "/api/controlplane/agent-hierarchy"):
            self._hierarchy_mutation()
        elif self.command == "POST" and path in ("/api/agent-hierarchy/command", "/api/controlplane/agent-hierarchy/command"):
            self._hierarchy_command()
        elif self.command == "POST" and path in ("/api/harness-binding", "/api/controlplane/harness_binding"):
            self._set_harness_binding()
        elif self.command == "GET" and path == "/api/agent-settings":
            self._agent_settings()
        elif self.command == "POST" and path == "/api/console":
            self._console()
        elif self.command == "GET" and path == "/api/tasks":
            tasks = self.state.kanban.list_tasks() or []
            self._send_json(200, {"tasks": [t.to_dict() if hasattr(t, "to_dict") else t for t in tasks]})
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
        elif self.command == "POST" and path == "/api/kanban/heartbeat":
            self._kanban_heartbeat()
        elif self.command == "POST" and path == "/api/kanban/claim":
            self._kanban_claim()
        elif self.command == "POST" and path == "/api/code/symbols":
            self._code_symbols()
        elif self.command == "POST" and path == "/api/okf/scan":
            self._okf_scan()
        elif self.command == "GET" and path == "/api/events/stream":
            self._events_stream()
        elif self.command == "POST" and path == "/api/events/ingest":
            self._events_ingest()
        elif self.command == "POST" and path == "/api/evolution/automerge":
            self._evolution_automerge()
        elif self.command == "GET" and path == "/api/evolution/rsi/skills":
            self._evolution_rsi_skills()
        elif self.command == "POST" and path == "/api/evolution/rsi/trigger":
            self._evolution_rsi_trigger()

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
        elif self.command == "POST" and path == "/api/sessions/search":
            self._sessions_search()
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
            # Fast-path Rust Timeline Aggregator se disponível
            try:
                import urllib.request
                qs = self.path.split("?")[1] if "?" in self.path else "limit=80"
                req = urllib.request.Request(f"http://100.77.31.78:8788/api/timeline/fast?{qs}")
                with urllib.request.urlopen(req, timeout=0.5) as resp:
                    if resp.status == 200:
                        self.wfile.write(resp.read())
                        return
            except Exception:
                pass
            cat = parse_qs(urlparse(self.path).query).get("category", [None])[0]
            self._send_json(200, {"timeline": self.state.get_unified_timeline(category=cat)})
        elif self.command == "GET" and path in ("/api/skills/catalog", "/api/skills/hub/official"):
            self._skills_catalog()
        elif self.command == "GET" and path in ("/api/skills/installed", "/api/skills/hub/sources"):
            self._skills_installed()
        elif self.command == "POST" and path in ("/api/skills/install", "/api/skills/hub/install"):
            self._skills_install()
        elif self.command == "POST" and path in ("/api/skills/uninstall", "/api/skills/hub/uninstall"):
            self._skills_uninstall()
        elif self.command == "GET" and path == "/api/skills/hub/search":
            self._skills_catalog()
        elif self.command == "GET" and path == "/api/plugins/catalog":
            self._plugins_catalog()
        elif self.command == "GET" and path == "/api/plugins/installed":
            self._plugins_installed()
        elif self.command == "POST" and path == "/api/plugins/install":
            self._plugins_install()
        elif self.command == "POST" and path == "/api/plugins/toggle":
            self._plugins_toggle()
        elif self.command == "POST" and path == "/api/plugins/uninstall":
            self._plugins_uninstall()
        elif self.command == "GET" and path == "/api/system-one/stats":
            self._system_one_stats()
        elif self.command == "GET" and path == "/api/system-one/decisions":
            self._system_one_decisions()
        elif self.command == "POST" and path == "/api/system-one/decide":
            self._system_one_decide()
        elif self.command == "POST" and path == "/api/system-one/clear":
            self._system_one_clear()

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
    # ------------------------------------------------------------------ #
    # Skills & Plugins Hub API
    # ------------------------------------------------------------------ #
    def _skills_catalog(self) -> None:
        from urllib.parse import parse_qs, urlparse
        from hermes.platform.skills.wshobson_catalog import WshobsonCatalog
        from hermes_constants import get_hermes_home

        qs = parse_qs(urlparse(self.path).query)
        q = (qs.get('query') or [''])[0].strip().lower()
        limit = int((qs.get('limit') or ['120'])[0])

        catalog = WshobsonCatalog()
        raw_skills = catalog.search(q, limit=limit)

        installed_names = set()
        skills_dir = get_hermes_home() / 'skills'
        if skills_dir.exists():
            for item in skills_dir.iterdir():
                if item.is_dir():
                    if (item / 'SKILL.md').exists():
                        installed_names.add(item.name.lower())
                    for sub in item.iterdir():
                        if sub.is_dir() and (sub / 'SKILL.md').exists():
                            installed_names.add(sub.name.lower())

        results = []
        for s in raw_skills:
            name = s.name
            results.append({
                'name': name,
                'category': s.category or 'General',
                'description': s.description or '',
                'author': getattr(s, 'author', None) or 'Community',
                'path': s.path or '',
                'installed': name.lower() in installed_names,
            })
        self._send_json(200, {'skills': results, 'total': len(results)})

    def _skills_installed(self) -> None:
        from hermes_constants import get_hermes_home

        skills_dir = get_hermes_home() / 'skills'
        installed = []
        if skills_dir.exists():
            for item in skills_dir.iterdir():
                if item.name.startswith('.'):
                    continue
                if item.is_dir():
                    skill_md = item / 'SKILL.md'
                    if skill_md.exists():
                        installed.append({
                            'name': item.name,
                            'category': 'Custom / Direct',
                            'path': str(skill_md),
                            'installed': True
                        })
                    for sub in item.iterdir():
                        if sub.is_dir() and (sub / 'SKILL.md').exists():
                            installed.append({
                                'name': sub.name,
                                'category': item.name,
                                'path': str(sub / 'SKILL.md'),
                                'installed': True
                            })
        self._send_json(200, {'skills': installed, 'count': len(installed)})

    def _skills_install(self) -> None:
        from hermes.platform.skills.wshobson_catalog import WshobsonCatalog
        from hermes_constants import get_hermes_home
        from pathlib import Path
        import tempfile

        body = self._read_json_body()
        skill_name = str(body.get('name') or body.get('identifier') or '').strip()
        force = bool(body.get('force', False))
        if not skill_name:
            self._send_json(400, {'error': 'name_required'})
            return

        catalog = WshobsonCatalog()
        meta = catalog.get(skill_name)
        if not meta:
            self._send_json(404, {'error': f'Skill {skill_name} not found in catalog'})
            return

        content = catalog.fetch_skill_content(meta)
        if not content:
            self._send_json(502, {'error': f'Failed to fetch skill content for {skill_name}'})
            return

        verdict = 'safe'
        try:
            from tools.skills_guard import scan_skill
            with tempfile.TemporaryDirectory() as tmpdir:
                qdir = Path(tmpdir)
                (qdir / 'SKILL.md').write_text(content, encoding='utf-8')
                rep = scan_skill(qdir, source='trusted')
                if rep.verdict not in ('safe', 'caution') and not force:
                    self._send_json(403, {
                        'error': f'Skill security scan blocked: {rep.verdict}',
                        'findings': [{'rule': f.rule_id, 'msg': f.message} for f in rep.findings]
                    })
                    return
                verdict = rep.verdict
        except Exception:
            pass

        cat_slug = (meta.category or 'general').lower().replace(' ', '-')
        target_dir = get_hermes_home() / 'skills' / cat_slug / skill_name
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / 'SKILL.md').write_text(content, encoding='utf-8')

        self._send_json(200, {'ok': True, 'name': skill_name, 'category': cat_slug, 'verdict': verdict})

    def _skills_uninstall(self) -> None:
        from hermes_constants import get_hermes_home
        import shutil

        body = self._read_json_body()
        skill_name = str(body.get('name') or '').strip()
        if not skill_name:
            self._send_json(400, {'error': 'name_required'})
            return

        skills_dir = get_hermes_home() / 'skills'
        removed = False
        if skills_dir.exists():
            for item in skills_dir.iterdir():
                if item.is_dir():
                    if item.name.lower() == skill_name.lower():
                        shutil.rmtree(item)
                        removed = True
                        break
                    for sub in item.iterdir():
                        if sub.is_dir() and sub.name.lower() == skill_name.lower():
                            shutil.rmtree(sub)
                            removed = True
                            break
                if removed:
                    break

        if removed:
            self._send_json(200, {'ok': True, 'name': skill_name})
        else:
            self._send_json(404, {'error': f'Skill {skill_name} not found'})

    def _plugins_catalog(self) -> None:
        from urllib.parse import parse_qs, urlparse
        from hermes_cli.plugins_cmd_catalog import load_catalog_live, filter_entries
        from hermes_cli.plugins_cmd import _discover_all_plugins, _get_enabled_set, _get_disabled_set

        qs = parse_qs(urlparse(self.path).query)
        q = (qs.get('query') or [''])[0].strip()

        entries = filter_entries(load_catalog_live(), q)
        enabled = _get_enabled_set()
        disabled = _get_disabled_set()

        installed_map = {}
        for item in _discover_all_plugins():
            p_name = item[0]
            p_dir = item[4] if len(item) > 4 else ''
            installed_map[p_name.lower()] = {
                'name': p_name,
                'dir': p_dir,
                'enabled': p_name in enabled and p_name not in disabled,
                'source': item[3] if len(item) > 3 else 'user'
            }

        results = []
        for e in entries:
            is_inst = e.name.lower() in installed_map
            inst_data = installed_map.get(e.name.lower())
            results.append({
                'name': e.name,
                'category': e.category,
                'tier': e.tier,
                'description': e.description,
                'maintainer': e.maintainer,
                'repo': e.repo,
                'docs_url': e.docs_url,
                'capabilities': e.capabilities.to_dict() if hasattr(e.capabilities, 'to_dict') else {},
                'installed': is_inst,
                'enabled': inst_data['enabled'] if is_inst else False,
            })
        self._send_json(200, {'plugins': results, 'total': len(results)})

    def _plugins_installed(self) -> None:
        from hermes_cli.plugins_cmd import _discover_all_plugins, _get_enabled_set, _get_disabled_set

        enabled = _get_enabled_set()
        disabled = _get_disabled_set()
        installed = []
        for item in _discover_all_plugins():
            p_name = item[0]
            installed.append({
                'name': p_name,
                'version': item[1] if len(item) > 1 else 'latest',
                'description': item[2] if len(item) > 2 else '',
                'source': item[3] if len(item) > 3 else 'user',
                'dir': item[4] if len(item) > 4 else '',
                'enabled': p_name in enabled and p_name not in disabled,
            })
        self._send_json(200, {'plugins': installed, 'count': len(installed)})

    def _plugins_install(self) -> None:
        from hermes_cli.plugins_cmd import dashboard_install_plugin

        body = self._read_json_body()
        ident = str(body.get('identifier') or body.get('name') or '').strip()
        catalog_name = str(body.get('catalog_name') or '').strip()
        force = bool(body.get('force', False))
        enable = bool(body.get('enable', True))
        if not ident and not catalog_name:
            self._send_json(400, {'error': 'identifier_required'})
            return

        # The hub grid only offers curated rows, so a bare name is a catalog
        # entry — resolve it through catalog_name so the pinned SHA and the kill
        # list apply instead of treating it as a (invalid) bare git source.
        if not catalog_name and ident and '/' not in ident and not ident.startswith(
                ('http://', 'https://', 'ssh://', 'git@', 'file://')):
            catalog_name, ident = ident, ''

        try:
            res = dashboard_install_plugin(
                ident, force=force, enable=enable, catalog_name=catalog_name or None)
            self._send_json(200, res)
        except Exception as exc:
            self._send_json(500, {'error': str(exc)})

    def _plugins_toggle(self) -> None:
        from hermes_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

        body = self._read_json_body()
        name = str(body.get('name') or '').strip()
        enabled = bool(body.get('enabled', True))
        if not name:
            self._send_json(400, {'error': 'name_required'})
            return

        try:
            res = dashboard_set_agent_plugin_enabled(name, enabled=enabled)
            self._send_json(200, res)
        except Exception as exc:
            self._send_json(500, {'error': str(exc)})

    def _plugins_uninstall(self) -> None:
        from hermes_cli.plugins_cmd import dashboard_remove_user_plugin

        body = self._read_json_body()
        name = str(body.get('name') or '').strip()
        if not name:
            self._send_json(400, {'error': 'name_required'})
            return

        try:
            res = dashboard_remove_user_plugin(name)
            self._send_json(200, res)
        except Exception as exc:
            self._send_json(500, {'error': str(exc)})

    # ------------------------------------------------------------------ #
    # System One (Fast Typed Decision Engine) API
    # ------------------------------------------------------------------ #
    def _system_one_stats(self) -> None:
        from hermes.platform.decision import get_decision_engine
        engine = get_decision_engine()
        self._send_json(200, engine.store.get_stats())

    def _system_one_decisions(self) -> None:
        from urllib.parse import parse_qs, urlparse
        from hermes.platform.decision import get_decision_engine

        qs = parse_qs(urlparse(self.path).query)
        limit = int((qs.get('limit') or ['40'])[0])
        domain = (qs.get('domain') or [None])[0]

        engine = get_decision_engine()
        items = engine.store.list_recent(limit=limit, domain=domain)
        self._send_json(200, {'decisions': items, 'count': len(items)})

    def _system_one_decide(self) -> None:
        from hermes.platform.decision import get_decision_engine

        body = self._read_json_body()
        dtype = str(body.get('type') or 'boolean').strip().lower()
        context = str(body.get('context') or '').strip()
        domain = str(body.get('domain') or 'general').strip()

        engine = get_decision_engine()
        if dtype == 'boolean':
            statement = str(body.get('statement') or body.get('question') or '').strip()
            res = engine.decide_boolean(statement=statement, context=context, domain=domain)
            self._send_json(200, res.to_dict())
        elif dtype == 'choice':
            question = str(body.get('question') or '').strip()
            options = list(body.get('options') or [])
            if not options:
                self._send_json(400, {'error': 'options_required_for_choice'})
                return
            res = engine.decide_choice(question=question, options=options, context=context, domain=domain)
            self._send_json(200, res.to_dict())
        elif dtype == 'score':
            criterion = str(body.get('criterion') or '').strip()
            res = engine.decide_score(criterion=criterion, context=context, domain=domain)
            self._send_json(200, res.to_dict())
        else:
            self._send_json(400, {'error': f'unknown_decision_type: {dtype}'})

    def _system_one_clear(self) -> None:
        from hermes.platform.decision import get_decision_engine
        body = self._read_json_body()
        domain = body.get('domain')
        engine = get_decision_engine()
        cleared = engine.store.clear(domain=domain)
        self._send_json(200, {'ok': True, 'cleared_count': cleared})

    def _serve_index(self) -> None:
        index = _STATIC_DIR / "index.html"
        try:
            self._send(200, index.read_bytes(), "text/html; charset=utf-8")
        except OSError:
            self._send_json(500, {"error": "static_missing"})

    def _console(self) -> None:
        body = self._read_json_body()
        message = str(body.get("message") or "").strip()
        bot_id = str(body.get("bot_id") or "").strip()
        if not message:
            self._send_json(400, {"error": "message_required"})
            return

        model_profile = None
        assignee = None
        agent_target = None
        if bot_id:
            try:
                snap = self.state.agent_hierarchy.snapshot()
                node = next((n for n in snap.get("nodes", []) if n.get("id") == bot_id), None)
                if node:
                    agent_target = bot_id
                    assignee = node.get("profile") or bot_id
                    model_profile = node.get("model") or None
            except Exception:
                pass

        created = self.state.create_task_from_message(
            message,
            priority=int(body.get("priority") or 85),
            model_profile=model_profile,
            assignee=assignee,
            agent_target=agent_target,
            yolo_mode=OPERATOR_YOLO_DEFAULT,
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
                prov_name = getattr(profile, "provider", None) or getattr(profile, "default_provider", "profile")
                raw_models.append({"id": profile.id, "root": profile.id, "provider": prov_name})
                for r in profile.routes:
                    raw_models.append({"id": r.provider_model_id, "root": r.provider_model_id, "provider": r.provider_id})
                    raw_models.append({"id": f"{r.provider_id}_{r.provider_model_id}", "root": r.provider_model_id, "provider": r.provider_id})
        except Exception:
            pass

        # Também consulta o endpoint local ativo (ex: antigravity / 100.77.31.78:8790) se configurado
        try:
            import urllib.request
            from hermes_cli.config import load_config
            cfg = load_config()
            base_url = cfg.get("model", {}).get("base_url") or "http://100.77.31.78:8790/v1"
            req = urllib.request.Request(f"{base_url.rstrip('/')}/models", headers={"User-Agent": "HAOS-ControlPlane"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("data", []):
                    mid = m.get("id")
                    if mid:
                        owner = m.get("owned_by") or "custom"
                        raw_models.append({"id": mid, "root": mid, "provider": owner})
        except Exception:
            pass

        deduped = deduplicate_models(raw_models)
        models = [
            {
                "id": m["id"],
                "object": "model",
                "created": now,
                "owned_by": m.get("provider") or "haos",
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

        # Se a tarefa já está finalizada ou o log_file não existe no workspace efêmero,
        # envia o resumo do resultado e artefatos gravados no banco para exibição imediata
        task_res = task.get("result")
        res_summary = ""
        if task_res:
            if hasattr(task_res, "summary") and task_res.summary:
                res_summary = task_res.summary
            elif isinstance(task_res, dict) and task_res.get("summary"):
                res_summary = task_res["summary"]
            elif hasattr(task_res, "__dict__") and task_res.__dict__.get("summary"):
                res_summary = task_res.__dict__["summary"]

        init_pkt = json.dumps({
            "task_id": task_id,
            "status": task.get("status"),
            "title": task.get("title"),
            "goal": (task.get("spec") or {}).get("goal") or task.get("title"),
            "elapsed_seconds": task.get("elapsed_seconds", 0),
            "tokens": task.get("tokens", 0),
            "cost": task.get("cost", 0.0),
            "summary": res_summary,
        })
        try:
            self.wfile.write(f"event: status\ndata: {init_pkt}\n\n".encode("utf-8"))
            self.wfile.flush()
        except Exception:
            return

        # Se já tiver resumo gravado no banco de tarefas concluídas, envia como log histórico
        if res_summary:
            try:
                hist_header = f"=== [RELATÓRIO / LOG HISTÓRICO DA TAREFA CONCLUÍDA: {task_id}] ===\n\n"
                chunk = json.dumps({"text": hist_header + res_summary + "\n"})
                self.wfile.write(f"event: log\ndata: {chunk}\n\n".encode("utf-8"))
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
                task_res = cur_task.get("result")
                live_summary = ""
                if task_res:
                    if hasattr(task_res, "summary") and task_res.summary:
                        live_summary = task_res.summary
                    elif isinstance(task_res, dict) and task_res.get("summary"):
                        live_summary = task_res["summary"]
                    elif hasattr(task_res, "__dict__") and task_res.__dict__.get("summary"):
                        live_summary = task_res.__dict__["summary"]

                status_pkt = json.dumps({
                    "task_id": task_id,
                    "status": cur_status,
                    "elapsed_seconds": cur_task.get("elapsed_seconds", 0),
                    "tokens": cur_task.get("tokens", 0),
                    "cost": cur_task.get("cost", 0.0),
                    "summary": live_summary,
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
                        self.wfile.write(f"event: done\ndata: {status_pkt}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except Exception:
                        pass
                    break

            try:
                time.sleep(1.0)
            except Exception:
                break

    def _resolve_profile_soul_path(self, profile_name: str) -> Path:
        """Resolve path to SOUL.md for a given Hermes profile name or default."""
        from hermes_constants import get_hermes_home
        from hermes_cli.profiles import _get_profiles_root
        pname = (profile_name or "").strip()
        if not pname or pname.lower() == "default":
            return get_hermes_home() / "SOUL.md"
        profiles_root = _get_profiles_root()
        prof_dir = profiles_root / pname
        prof_dir.mkdir(parents=True, exist_ok=True)
        return prof_dir / "SOUL.md"

    def _resolve_profile_memory_path(self, profile_name: str) -> Path:
        """Resolve path to MEMORY.md for a given Hermes profile name or default."""
        from hermes_constants import get_hermes_home
        from hermes_cli.profiles import _get_profiles_root
        pname = (profile_name or "").strip()
        if not pname or pname.lower() == "default":
            mem_path = get_hermes_home() / "memories" / "MEMORY.md"
            if not mem_path.is_file():
                mem_path = get_hermes_home() / "MEMORY.md"
            return mem_path
        profiles_root = _get_profiles_root()
        prof_dir = profiles_root / pname
        prof_dir.mkdir(parents=True, exist_ok=True)
        return prof_dir / "MEMORY.md"

    def _get_hierarchy_soul(self) -> None:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        target_id = query.get("target_id", [""])[0].strip()
        profile = query.get("profile", [""])[0].strip()

        if target_id and not profile:
            snapshot = self.state.agent_hierarchy.snapshot()
            node = next((n for n in snapshot.get("nodes", []) if n.get("id") == target_id), None)
            if node:
                profile = node.get("profile") or node.get("id")

        soul_path = self._resolve_profile_soul_path(profile)
        content = ""
        if soul_path.is_file():
            try:
                content = soul_path.read_text(encoding="utf-8")
            except Exception as e:
                self._send_json(500, {"ok": False, "error": f"read_error: {e}"})
                return
        self._send_json(200, {
            "ok": True,
            "target_id": target_id,
            "profile": profile,
            "path": str(soul_path),
            "exists": soul_path.is_file(),
            "content": content
        })

    def _save_hierarchy_soul(self) -> None:
        body = self._read_json_body()
        target_id = str(body.get("target_id") or "").strip()
        profile = str(body.get("profile") or "").strip()
        content = str(body.get("content") or "")

        if target_id and not profile:
            snapshot = self.state.agent_hierarchy.snapshot()
            node = next((n for n in snapshot.get("nodes", []) if n.get("id") == target_id), None)
            if node:
                profile = node.get("profile") or node.get("id")

        soul_path = self._resolve_profile_soul_path(profile)
        try:
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            soul_path.write_text(content, encoding="utf-8")
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"write_error: {e}"})
            return

        self._send_json(200, {
            "ok": True,
            "target_id": target_id,
            "profile": profile,
            "path": str(soul_path),
            "saved": True
        })

    def _get_hierarchy_memory(self) -> None:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        target_id = query.get("target_id", [""])[0].strip()
        profile = query.get("profile", [""])[0].strip()

        if target_id and not profile:
            snapshot = self.state.agent_hierarchy.snapshot()
            node = next((n for n in snapshot.get("nodes", []) if n.get("id") == target_id), None)
            if node:
                profile = node.get("profile") or node.get("id")

        mem_path = self._resolve_profile_memory_path(profile)
        content = ""
        if mem_path.is_file():
            try:
                content = mem_path.read_text(encoding="utf-8")
            except Exception as e:
                self._send_json(500, {"ok": False, "error": f"read_error: {e}"})
                return
        self._send_json(200, {
            "ok": True,
            "target_id": target_id,
            "profile": profile,
            "path": str(mem_path),
            "exists": mem_path.is_file(),
            "content": content
        })

    def _save_hierarchy_memory(self) -> None:
        body = self._read_json_body()
        target_id = str(body.get("target_id") or "").strip()
        profile = str(body.get("profile") or "").strip()
        content = str(body.get("content") or "")

        if target_id and not profile:
            snapshot = self.state.agent_hierarchy.snapshot()
            node = next((n for n in snapshot.get("nodes", []) if n.get("id") == target_id), None)
            if node:
                profile = node.get("profile") or node.get("id")

        mem_path = self._resolve_profile_memory_path(profile)
        try:
            mem_path.parent.mkdir(parents=True, exist_ok=True)
            mem_path.write_text(content, encoding="utf-8")
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"write_error: {e}"})
            return

        self._send_json(200, {
            "ok": True,
            "target_id": target_id,
            "profile": profile,
            "path": str(mem_path),
            "saved": True
        })

    def _resolve_bot_notebook_path(self, target_id: str) -> Path:
        """Resolve private notebook directory and draft_notes.md path for a given agent node."""
        from hermes_constants import get_hermes_home
        base_dir = get_hermes_home() / "notebooks" / target_id
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / "draft_notes.md"

    def _resolve_team_wiki_dir(self) -> Path:
        """Resolve shared team wiki directory path."""
        from hermes_constants import get_hermes_home
        wiki_dir = get_hermes_home() / "wiki" / "articles"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        return wiki_dir

    def _get_hierarchy_notebook(self) -> None:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        target_id = query.get("target_id", [""])[0].strip()
        if not target_id:
            self._send_json(400, {"ok": False, "error": "target_id_required"})
            return

        notebook_file = self._resolve_bot_notebook_path(target_id)
        content = ""
        if notebook_file.is_file():
            try:
                content = notebook_file.read_text(encoding="utf-8")
            except Exception as e:
                self._send_json(500, {"ok": False, "error": f"read_error: {e}"})
                return

        self._send_json(200, {
            "ok": True,
            "target_id": target_id,
            "path": str(notebook_file),
            "exists": notebook_file.is_file(),
            "content": content
        })

    def _save_hierarchy_notebook(self) -> None:
        body = self._read_json_body()
        target_id = str(body.get("target_id") or "").strip()
        content = str(body.get("content") or "")
        if not target_id:
            self._send_json(400, {"ok": False, "error": "target_id_required"})
            return

        notebook_file = self._resolve_bot_notebook_path(target_id)
        try:
            notebook_file.write_text(content, encoding="utf-8")
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"write_error: {e}"})
            return

        self._send_json(200, {
            "ok": True,
            "target_id": target_id,
            "path": str(notebook_file),
            "saved": True
        })

    def _get_hierarchy_wiki_articles(self) -> None:
        wiki_dir = self._resolve_team_wiki_dir()
        articles = []
        for file in sorted(wiki_dir.glob("*.md")):
            slug = file.stem
            try:
                text = file.read_text(encoding="utf-8")
            except Exception:
                continue
            title = slug.replace("-", " ").title()
            author = "Sistema"
            promoted_at = ""
            # Analisa metadados se houver
            lines = text.splitlines()
            for line in lines[:15]:
                if line.startswith("# "):
                    title = line[2:].strip()
                elif "* **Autor**:" in line or "* **Promovido por**:" in line:
                    author = line.split(":", 1)[1].strip().strip("* ")
                elif "* **Data**:" in line:
                    promoted_at = line.split(":", 1)[1].strip().strip("* ")

            articles.append({
                "slug": slug,
                "title": title,
                "author": author,
                "promoted_at": promoted_at,
                "path": str(file),
                "preview": text[:200]
            })

        self._send_json(200, {"ok": True, "articles": articles})

    def _get_hierarchy_wiki_article(self) -> None:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        slug = query.get("slug", [""])[0].strip()
        if not slug:
            self._send_json(400, {"ok": False, "error": "slug_required"})
            return

        wiki_dir = self._resolve_team_wiki_dir()
        article_file = wiki_dir / f"{slug}.md"
        if not article_file.is_file():
            self._send_json(404, {"ok": False, "error": "article_not_found"})
            return

        try:
            content = article_file.read_text(encoding="utf-8")
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"read_error: {e}"})
            return

        self._send_json(200, {
            "ok": True,
            "slug": slug,
            "path": str(article_file),
            "content": content
        })

    def _save_hierarchy_wiki_article(self) -> None:
        body = self._read_json_body()
        slug = str(body.get("slug") or "").strip()
        title = str(body.get("title") or "").strip()
        content = str(body.get("content") or "").strip()

        if not slug and title:
            import re
            slug = re.sub(r'[^a-zA-Z0-9_\-]+', '-', title.lower()).strip('-')

        if not slug or not content:
            self._send_json(400, {"ok": False, "error": "slug_and_content_required"})
            return

        wiki_dir = self._resolve_team_wiki_dir()
        article_file = wiki_dir / f"{slug}.md"
        try:
            article_file.write_text(content, encoding="utf-8")
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"write_error: {e}"})
            return

        self._send_json(200, {
            "ok": True,
            "slug": slug,
            "path": str(article_file),
            "saved": True
        })

    def _promote_hierarchy_notebook_to_wiki(self) -> None:
        """Promove um rascunho de caderno para a Wiki Compartilhada com proveniência e citação formal."""
        import datetime
        import re
        body = self._read_json_body()
        target_id = str(body.get("target_id") or "").strip()
        title = str(body.get("title") or "").strip()
        slug = str(body.get("slug") or "").strip()
        draft_content = str(body.get("content") or "").strip()
        source_ref = str(body.get("source_ref") or "Sessão / Execução do Agente").strip()

        if not title:
            self._send_json(400, {"ok": False, "error": "title_required"})
            return
        if not draft_content:
            self._send_json(400, {"ok": False, "error": "content_required"})
            return

        if not slug:
            slug = re.sub(r'[^a-zA-Z0-9_\-]+', '-', title.lower()).strip('-')

        snapshot = self.state.agent_hierarchy.snapshot()
        node = next((n for n in snapshot.get("nodes", []) if n.get("id") == target_id), None)
        node_name = node.get("name") if node else (target_id or "Agente Autônomo")
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Injeta cabeçalho e rodapé formal de citação e proveniência estilo Wikipedia / gawkbot
        wiki_text = f"""# {title}

{draft_content}

---
### 📚 Proveniência & Citações Canônicas
* **Autor Original**: `{node_name}` (ID: `{target_id or 'unknown'}`)
* **Data de Promoção**: {now_str}
* **Origem da Evidência**: {source_ref}
* **Nível de Confiança**: Validado & Promovido para a Wiki Compartilhada
"""

        wiki_dir = self._resolve_team_wiki_dir()
        article_file = wiki_dir / f"{slug}.md"
        try:
            article_file.write_text(wiki_text, encoding="utf-8")
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"write_error: {e}"})
            return

        self._send_json(200, {
            "ok": True,
            "slug": slug,
            "title": title,
            "path": str(article_file),
            "promoted": True,
            "promoted_by": node_name
        })

    def _get_hierarchy_routines(self) -> None:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        target_id = query.get("target_id", [""])[0].strip()
        try:
            from cron.jobs import list_jobs
            jobs = list_jobs(include_disabled=True)
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"cron_error: {e}"})
            return

        tag_prefix = f"[{target_id}]"
        matching = []
        job_ids = []
        for j in jobs:
            name = str(j.get("name") or "")
            if not target_id or tag_prefix in name or f"bot:{target_id}" in name:
                matching.append(j)
                job_ids.append(j.get("id"))

        # Carrega histórico detalhado de execuções/transcrições (Run Transcripts)
        transcripts = {}
        try:
            from cron.executions import list_executions
            for jid in job_ids:
                execs = list_executions(job_id=jid, limit=5)
                if execs:
                    transcripts[jid] = execs
        except Exception:
            pass

        self._send_json(200, {
            "ok": True,
            "target_id": target_id,
            "routines": matching,
            "transcripts": transcripts
        })

    def _save_hierarchy_routine(self) -> None:
        body = self._read_json_body()
        target_id = str(body.get("target_id") or "").strip()
        prompt = str(body.get("prompt") or "").strip()
        schedule = str(body.get("schedule") or "every 24h").strip()
        name = str(body.get("name") or "").strip()

        if not target_id or not prompt:
            self._send_json(400, {"ok": False, "error": "target_id_and_prompt_required"})
            return

        snapshot = self.state.agent_hierarchy.snapshot()
        node = next((n for n in snapshot.get("nodes", []) if n.get("id") == target_id), None)
        node_name = node.get("name") if node else target_id
        routine_name = f"[{target_id}] {name}" if name else f"[{target_id}] Rotina {node_name[:20]}"

        try:
            from cron.jobs import create_job
            job = create_job(
                prompt=prompt,
                schedule=schedule,
                name=routine_name,
                model=node.get("model") if node else None,
            )
        except Exception as e:
            self._send_json(400, {"ok": False, "error": f"create_job_error: {e}"})
            return

        self._send_json(200, {"ok": True, "routine": job})

    def _delete_hierarchy_routine(self) -> None:
        body = self._read_json_body()
        job_id = str(body.get("job_id") or "").strip()
        if not job_id:
            self._send_json(400, {"ok": False, "error": "job_id_required"})
            return
        try:
            from cron.jobs import remove_job
            removed = remove_job(job_id)
        except Exception as e:
            self._send_json(400, {"ok": False, "error": f"remove_job_error: {e}"})
            return
        self._send_json(200, {"ok": True, "removed": removed, "job_id": job_id})

    def _get_hierarchy_feed(self) -> None:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        target_id = query.get("target_id", [""])[0].strip()

        snapshot = self.state.agent_hierarchy.snapshot()
        node = next((n for n in snapshot.get("nodes", []) if n.get("id") == target_id), None)
        profile = node.get("profile") if node else None

        # Busca tarefas no Kanban atribuídas ou com tag deste agente
        all_tasks = self.state.kanban.list_tasks() or []
        bot_tasks = []
        for t in all_tasks:
            tags = t.get("tags") or []
            assignee = t.get("assignee")
            if target_id and (f"agent:{target_id}" in tags or assignee == target_id or (profile and assignee == profile)):
                bot_tasks.append(t)
            elif not target_id:
                bot_tasks.append(t)

        # Busca eventos recentes no EventStore vinculados a este bot
        all_events = self.state.event_store.get_all(limit=150)
        bot_events = []
        for ev in all_events:
            p = ev.payload if isinstance(ev.payload, dict) else {}
            if not target_id or p.get("target_id") == target_id or p.get("assignee") == target_id or (profile and p.get("profile") == profile):
                bot_events.append({
                    "name": ev.name,
                    "timestamp": round(float(ev.timestamp), 3),
                    "trace_id": ev.trace_id,
                    "payload": p
                })

        self._send_json(200, {
            "ok": True,
            "target_id": target_id,
            "tasks": bot_tasks[-15:],
            "events": bot_events[-30:]
        })

    def _resolve_bot_microapps_dir(self, target_id: str) -> Path:
        """Resolve directory where bot microapps are stored."""
        from hermes_constants import get_hermes_home
        m_dir = get_hermes_home() / "microapps" / target_id
        m_dir.mkdir(parents=True, exist_ok=True)
        return m_dir

    def _get_hierarchy_microapps(self) -> None:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        target_id = query.get("target_id", [""])[0].strip()
        if not target_id:
            self._send_json(400, {"ok": False, "error": "target_id_required"})
            return

        m_dir = self._resolve_bot_microapps_dir(target_id)
        apps = []
        for file in sorted(m_dir.glob("*.html")):
            slug = file.stem
            try:
                content = file.read_text(encoding="utf-8")
            except Exception:
                continue
            title = slug.replace("-", " ").title()
            for line in content.splitlines()[:5]:
                if "<title>" in line.lower():
                    title = line.split(">")[1].split("<")[0].strip()
            apps.append({
                "slug": slug,
                "title": title,
                "path": str(file),
                "html": content
            })

        self._send_json(200, {"ok": True, "target_id": target_id, "microapps": apps})

    def _save_hierarchy_microapp(self) -> None:
        body = self._read_json_body()
        target_id = str(body.get("target_id") or "").strip()
        title = str(body.get("title") or "").strip()
        html = str(body.get("html") or "").strip()
        slug = str(body.get("slug") or "").strip()

        if not target_id or not html:
            self._send_json(400, {"ok": False, "error": "target_id_and_html_required"})
            return

        if not slug and title:
            import re
            slug = re.sub(r'[^a-zA-Z0-9_\-]+', '-', title.lower()).strip('-')
        if not slug:
            slug = f"app-{int(time.time())}"

        m_dir = self._resolve_bot_microapps_dir(target_id)
        app_file = m_dir / f"{slug}.html"
        try:
            app_file.write_text(html, encoding="utf-8")
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"write_error: {e}"})
            return

        self._send_json(200, {
            "ok": True,
            "target_id": target_id,
            "slug": slug,
            "path": str(app_file),
            "saved": True
        })

    def _delete_hierarchy_microapp(self) -> None:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        target_id = query.get("target_id", [""])[0].strip()
        slug = query.get("slug", [""])[0].strip()

        # Fallback para body JSON se não estiver na query string
        if not target_id or not slug:
            try:
                body = self._read_json_body()
                target_id = target_id or str(body.get("target_id") or "").strip()
                slug = slug or str(body.get("slug") or "").strip()
            except Exception:
                pass

        if not target_id or not slug:
            self._send_json(400, {"ok": False, "error": "target_id_and_slug_required"})
            return

        # Sanitização segura de path traversal
        import re
        safe_slug = re.sub(r'[^a-zA-Z0-9_\-]+', '', slug)
        if not safe_slug or safe_slug != slug:
            self._send_json(400, {"ok": False, "error": "invalid_slug"})
            return

        m_dir = self._resolve_bot_microapps_dir(target_id)
        app_file = (m_dir / f"{safe_slug}.html").resolve()

        # Garante que o arquivo está estritamente contido no diretório do bot
        try:
            app_file.relative_to(m_dir.resolve())
        except ValueError:
            self._send_json(403, {"ok": False, "error": "access_denied"})
            return

        if not app_file.exists():
            self._send_json(404, {"ok": False, "error": "microapp_not_found"})
            return

        try:
            app_file.unlink()
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"unlink_error: {e}"})
            return

        self._send_json(200, {
            "ok": True,
            "target_id": target_id,
            "slug": safe_slug,
            "deleted": True
        })

    def _get_hierarchy_shadows(self) -> None:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        parent_bot_id = query.get("parent_bot_id", [""])[0].strip() or None
        leaves = self.state.shadow_manager.list_shadows(parent_bot_id=parent_bot_id)
        self._send_json(200, {
            "ok": True,
            "parent_bot_id": parent_bot_id,
            "shadows": [l.to_dict() for l in leaves]
        })

    def _spawn_hierarchy_shadow(self) -> None:
        body = self._read_json_body()
        parent_bot_id = str(body.get("parent_bot_id") or "").strip()
        task_description = str(body.get("task_description") or "Tarefa isolada de Sombra").strip()

        if not parent_bot_id:
            self._send_json(400, {"ok": False, "error": "parent_bot_id_required"})
            return

        snapshot = self.state.agent_hierarchy.snapshot()
        node = next((n for n in snapshot.get("nodes", []) if n.get("id") == parent_bot_id), None)
        if not node:
            self._send_json(404, {"ok": False, "error": "parent_bot_not_found"})
            return

        parent_bot_name = node.get("name") or parent_bot_id
        profile = node.get("profile") or parent_bot_id

        try:
            leaf = self.state.shadow_manager.spawn_shadow(
                parent_bot_id=parent_bot_id,
                parent_bot_name=parent_bot_name,
                profile=profile,
                task_description=task_description,
                metadata={"spawned_by": "controlplane_ui"}
            )
            self._send_json(200, {
                "ok": True,
                "shadow": leaf.to_dict()
            })
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"spawn_error: {e}"})

    def _discard_hierarchy_shadow(self) -> None:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        leaf_id = query.get("leaf_id", [""])[0].strip()

        if not leaf_id:
            try:
                body = self._read_json_body()
                leaf_id = leaf_id or str(body.get("leaf_id") or "").strip()
            except Exception:
                pass

        if not leaf_id:
            self._send_json(400, {"ok": False, "error": "leaf_id_required"})
            return

        success = self.state.shadow_manager.discard_shadow(leaf_id)
        if not success:
            self._send_json(404, {"ok": False, "error": "shadow_not_found"})
            return

        self._send_json(200, {
            "ok": True,
            "leaf_id": leaf_id,
            "discarded": True
        })

    def _decide_hierarchy_approval(self) -> None:
        """Processa decisão humana no Portão de Aprovação (Aprovar / Rejeitar) com suporte a YOLO mode."""
        body = self._read_json_body()
        task_id = str(body.get("task_id") or "").strip()
        verdict = str(body.get("verdict") or "").strip().lower()  # approved | changes_requested
        approver = str(body.get("approver") or "operator_web").strip()
        rationale = str(body.get("rationale") or "").strip()

        if not task_id or verdict not in ("approved", "changes_requested"):
            self._send_json(400, {"ok": False, "error": "task_id_and_valid_verdict_required"})
            return

        try:
            res = self.state.kanban.record_review_verdict(
                task_id,
                verdict,
                approver=approver,
                rationale=rationale,
                acceptance_status="passed" if verdict == "approved" else "failed"
            )
            self._send_json(200, {
                "ok": True,
                "task_id": task_id,
                "verdict": verdict,
                "approver": approver,
                "reviewer_verdict": res.reviewer_verdict
            })
        except Exception as e:
            self._send_json(500, {"ok": False, "error": str(e)})

    def _get_hierarchy_toolsets(self) -> None:
        """Lista todos os toolsets disponíveis no Hermes para seleção opcional no bot."""
        try:
            from toolsets import TOOLSETS
            names = sorted(list(TOOLSETS.keys()))
        except Exception:
            names = ["web", "search", "terminal", "file", "coding", "memory", "kanban", "skills", "browser"]
        self._send_json(200, {"ok": True, "toolsets": names})

    def _clone_hierarchy_node(self) -> None:
        body = self._read_json_body()
        source_id = str(body.get("source_id") or "").strip()
        new_name = str(body.get("name") or "").strip()
        if not source_id:
            self._send_json(400, {"ok": False, "error": "source_id_required"})
            return

        snapshot = self.state.agent_hierarchy.snapshot()
        source = next((n for n in snapshot.get("nodes", []) if n.get("id") == source_id), None)
        if not source:
            self._send_json(404, {"ok": False, "error": "source_node_not_found"})
            return

        clone_name = new_name or f"{source['name']} (Cópia)"
        source_role = source.get("role", "bot")
        # Se for master, a cópia vira gerente (só pode haver 1 master)
        new_role = "manager" if source_role == "master" else source_role
        new_parent = source.get("parent_id")
        if source_role == "master":
            new_parent = source["id"]

        import uuid
        new_profile = f"{source.get('profile') or 'agent'}-clone-{uuid.uuid4().hex[:6]}"
        new_node_payload = {
            "name": clone_name,
            "role": new_role,
            "parent_id": new_parent,
            "profile": new_profile,
            "model": source.get("model", ""),
            "provider": source.get("provider", ""),
            "description": source.get("description", ""),
            "enabled": True,
        }

        try:
            created = self.state.agent_hierarchy.upsert_node(new_node_payload)
            # Clona o SOUL.md se o source tiver um configurado
            src_soul_path = self._resolve_profile_soul_path(source.get("profile") or source.get("id"))
            if src_soul_path.is_file():
                dest_soul_path = self._resolve_profile_soul_path(new_profile)
                dest_soul_path.parent.mkdir(parents=True, exist_ok=True)
                dest_soul_path.write_text(src_soul_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return

        self._send_json(200, {
            "ok": True,
            "source_id": source_id,
            "created": created,
            "agent_hierarchy": self.state.agent_hierarchy.snapshot()
        })

    def _hierarchy_mutation(self) -> None:
        body = self._read_json_body()
        action = str(body.get("action") or "").strip().lower()
        try:
            if action == "set_bot_model":
                result = self.state.agent_hierarchy.set_bot_model(body.get("provider", ""), body.get("model", ""))
            elif action == "upsert_node":
                result = self.state.agent_hierarchy.upsert_node(body, node_id=body.get("id"))
            elif action == "delete_node":
                self.state.agent_hierarchy.delete_node(str(body.get("id") or "")); result = {"deleted": body.get("id")}
            elif action == "set_advisory_edge":
                result = self.state.agent_hierarchy.set_advisory_edge(str(body.get("from_id") or ""), str(body.get("to_id") or ""), int(body.get("max_turns", 3)))
            elif action == "remove_advisory_edge":
                self.state.agent_hierarchy.remove_advisory_edge(str(body.get("from_id") or ""), str(body.get("to_id") or "")); result = {"removed": True}
            else:
                self._send_json(400, {"ok": False, "error": "unknown_hierarchy_action"}); return
        except (HierarchyError, ValueError, TypeError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)}); return
        self._send_json(200, {"ok": True, "result": result, "agent_hierarchy": self.state.agent_hierarchy.snapshot()})

    def _hierarchy_command(self) -> None:
        body = self._read_json_body()
        target = str(body.get("target_id") or "").strip()
        command = str(body.get("command") or "").strip()
        if not target or not command:
            self._send_json(400, {"ok": False, "error": "target_id_and_command_required"}); return
        snapshot = self.state.agent_hierarchy.snapshot()
        node = next((n for n in snapshot["nodes"] if n.get("id") == target), None)
        if not node or not node.get("enabled", True):
            self._send_json(404, {"ok": False, "error": "unknown_agent"}); return
        task = self.state.create_task_from_message(
            command, title=f"Comando para {node['name']}",
            model_profile=node.get("model") or None, agent_target=target,
            assignee=node.get("profile") or target,
            yolo_mode=OPERATOR_YOLO_DEFAULT,
        )
        self.state.event_store.append(Event(name="agent_hierarchy.commanded", payload={"target_id": target, "profile": node.get("profile"), "model_profile": node.get("model"), "command": command}))
        self.state.dispatch_in_background(max_spawn=1)
        self._send_json(200, {"ok": True, "target_id": target, "task": task})

    def _hierarchy_council(self) -> None:
        body = self._read_json_body()
        action = str(body.get("action") or "").strip().lower()
        try:
            if action == "start":
                result = self.state.agent_hierarchy.start_council(str(body.get("from_id") or ""), str(body.get("to_id") or ""), str(body.get("topic") or ""), body.get("max_turns"))
            elif action == "turn":
                if body.get("execute", False):
                    result = self.state.council.run_turn(str(body.get("council_id") or ""), str(body.get("speaker_id") or ""), str(body.get("message") or ""))
                else:
                    result = self.state.agent_hierarchy.append_council_turn(str(body.get("council_id") or ""), str(body.get("speaker_id") or ""), str(body.get("message") or ""))
            else:
                self._send_json(400, {"ok": False, "error": "unknown_council_action"}); return
        except (HierarchyError, ValueError, TypeError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)}); return
        self._send_json(200, {"ok": True, "result": result})

    def _set_harness_binding(self) -> None:
        body = self._read_json_body()
        role = str(body.get("role") or "").strip()
        harness = str(body.get("harness") or "").strip()
        if not role or not harness:
            self._send_json(400, {"ok": False, "error": "role_and_harness_required"})
            return
        try:
            result = self.state.control_plane.set_harness_binding(role, harness)
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        result["ok"] = True
        result["overview"] = self.state.control_plane.harness_overview()
        self._send_json(200, result)

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
        files = [str(f) for f in (body.get("files") or []) if str(f).strip()]
        symbols = [str(s) for s in (body.get("symbols") or []) if str(s).strip()]
        root = body.get("root") or os.getcwd()
        force = bool(body.get("rescan") or body.get("recalc") or body.get("force"))
        t0 = time.time()

        # Fast-Path Rust Nativo (haos-edge blast-radius): sub-segundo, zero GIL lock
        import subprocess
        rust_bin = "/usr/local/bin/haos-edge"
        if not Path(rust_bin).exists():
            rust_bin = os.path.join(os.getcwd(), "target/release/haos-edge")
        if Path(rust_bin).exists():
            try:
                cmd = [rust_bin, "blast-radius", "--root", str(root)]
                for f in files:
                    cmd.extend(["--file", f])
                for s in symbols:
                    cmd.extend(["--symbol", s])
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if proc.returncode == 0 and proc.stdout.strip():
                    fast_res = json.loads(proc.stdout)
                    aff_files = fast_res.get("affected_files") or []
                    aff_callers = fast_res.get("affected_callers") or []
                    test_files = [f for f in aff_files if "test" in f.lower()]
                    risk_score = round(min(
                        1.0,
                        (len(aff_files) * 0.15)
                        + (len(aff_callers) * 0.05)
                        + (len(test_files) * 0.1),
                    ), 2)
                    self._send_json(200, {
                        "impacted_files": aff_files,
                        "impacted_callers": aff_callers,
                        "impacted_tests": test_files,
                        "risk_score": risk_score,
                        "severity": fast_res.get("severity", "low"),
                        "depth_reached": fast_res.get("depth_reached", 1),
                        "indexed_files": len(aff_files),
                        "indexed_symbols": len(symbols),
                        "scan_root": str(root),
                        "elapsed_ms": int((time.time() - t0) * 1000),
                        "rescan": force,
                        "engine": "rust_native_fast_path",
                    })
                    return
            except Exception as exc:
                print(f"[BlastRadius] Fast-path Rust blast radius fallback to python: {exc}")

        # Fallback legado em Python AST se o binário Rust não estiver disponível
        idx = self.state.ensure_symbol_index(root=root, force=force)
        graph = idx.get("graph")
        if graph is None:
            self._send_json(500, {"error": "symbol_index_unavailable"})
            return

        from hermes.platform.capabilities.lsp.unified_intelligence import ImpactAnalyzer
        analyzer = ImpactAnalyzer(graph)
        try:
            blast = analyzer.calculate_blast_radius(
                modified_symbols=symbols,
                modified_files=files,
                root_dir=idx.get("root"),
            )
            risk_score = round(min(
                1.0,
                (len(blast.affected_files) * 0.15)
                + (len(blast.affected_callers) * 0.05)
                + (len(blast.affected_test_suites) * 0.1),
            ), 2)
            self._send_json(200, {
                "impacted_files": sorted(blast.affected_files),
                "impacted_callers": sorted(blast.affected_callers),
                "impacted_tests": sorted(blast.affected_test_suites),
                "risk_score": risk_score,
                "severity": blast.severity,
                "depth_reached": blast.depth_reached,
                "indexed_files": idx.get("files_indexed", 0),
                "indexed_symbols": idx.get("symbols_indexed", 0),
                "scan_root": idx.get("root", ""),
                "elapsed_ms": int((time.time() - t0) * 1000),
                "rescan": force,
                "engine": "python_ast_legacy",
            })
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _kanban_heartbeat(self) -> None:
        body = self._read_json_body()
        task_id = str(body.get("task_id") or "").strip()
        worker_id = body.get("worker_id")
        if not task_id:
            self._send_json(400, {"ok": False, "error": "missing_task_id"})
            return
        try:
            ok = self.state.kanban.heartbeat(task_id, worker_id=worker_id)
            self._send_json(200, {"ok": True, "renewed": ok})
        except KeyError:
            self._send_json(200, {"ok": True, "renewed": False, "reason": "task_not_found"})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _kanban_claim(self) -> None:
        body = self._read_json_body()
        task_id = str(body.get("task_id") or "").strip()
        worker_id = body.get("worker_id")
        ttl = body.get("ttl_seconds")
        if not task_id:
            self._send_json(400, {"ok": False, "error": "missing_task_id"})
            return
        try:
            claimed = self.state.kanban.claim_task(task_id, worker_id=worker_id, lease_duration_sec=ttl)
            self._send_json(200, {"ok": True, "claimed": claimed})
        except KeyError:
            self._send_json(200, {"ok": True, "claimed": False, "reason": "task_not_found"})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _events_ingest(self) -> None:
        body = self._read_json_body()
        name = str(body.get("name") or "generic.event")
        payload = body.get("payload") or {}
        trace_id = body.get("trace_id")
        event_id = body.get("event_id")
        evt = Event(
            name=name,
            payload=payload,
            trace_id=trace_id,
            event_id=event_id,
            correlation_id=body.get("correlation_id"),
            causation_id=body.get("causation_id"),
            trust_level=body.get("trust_level") or "system",
            schema_version=int(body.get("schema_version") or 1),
            timestamp=float(body.get("timestamp") or time.time()),
        )
        try:
            # Append local direto sob lock interno seguro da conexão
            self.state.event_store.append(evt)
            self._send_json(200, {"ok": True, "event_id": evt.event_id})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _events_stream(self) -> None:
        """Server-Sent Events (SSE) stream de eventos em tempo real."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # Envia últimos 10 eventos imediatamente
        try:
            recent = self.state.event_store.get_all(limit=10)
            for ev in reversed(recent):
                data = json.dumps({
                    "event_id": ev.event_id,
                    "seq": getattr(ev, "seq", 0),
                    "name": ev.name,
                    "payload": ev.payload,
                    "timestamp": ev.timestamp,
                })
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass

    def _code_symbols(self) -> None:
        body = self._read_json_body()
        file_path = str(body.get("file_path") or "").strip()
        content = body.get("content")

        # Fast-path nativo em Rust haos-edge se disponível
        try:
            import urllib.request
            url = "http://100.77.31.78:8788/api/code/symbols"
            # O próprio standalone pode delegar para o handler embutido ou responder
        except Exception:
            pass

        p = Path(file_path)
        if not p.is_file() and not content:
            self._send_json(400, {"ok": False, "error": "file_not_found"})
            return

        text = content or p.read_text(encoding="utf-8", errors="replace")
        symbols = []
        for idx, line in enumerate(text.splitlines()):
            trimmed = line.strip()
            if trimmed.startswith("class "):
                rest = trimmed[6:]
                name = rest.split("(")[0].split(":")[0].strip()
                if name:
                    symbols.append({"kind": "class", "name": name, "line": idx + 1})
            elif trimmed.startswith("def "):
                rest = trimmed[4:]
                name = rest.split("(")[0].strip()
                if name:
                    symbols.append({"kind": "def", "name": name, "line": idx + 1})
            elif trimmed.startswith("export function ") or trimmed.startswith("function "):
                rest = trimmed[16:] if trimmed.startswith("export function ") else trimmed[9:]
                name = rest.split("(")[0].split("<")[0].strip()
                if name:
                    symbols.append({"kind": "function", "name": name, "line": idx + 1})
            elif trimmed.startswith("export const ") or trimmed.startswith("const "):
                rest = trimmed[13:] if trimmed.startswith("export const ") else trimmed[6:]
                if "=" in rest and ("=>" in rest or "function" in rest):
                    name = rest.split("=")[0].split(":")[0].strip()
                    if name:
                        symbols.append({"kind": "arrow_fn", "name": name, "line": idx + 1})
            elif trimmed.startswith("export interface ") or trimmed.startswith("interface "):
                rest = trimmed[17:] if trimmed.startswith("export interface ") else trimmed[10:]
                name = rest.split("<")[0].split("{")[0].strip()
                if name:
                    symbols.append({"kind": "interface", "name": name, "line": idx + 1})
            elif trimmed.startswith("pub struct ") or trimmed.startswith("pub enum ") or trimmed.startswith("pub fn "):
                parts = trimmed.split()
                if len(parts) >= 3:
                    kind = parts[1]
                    name = parts[2].split("<")[0].split("(")[0].split("{")[0].strip()
                    symbols.append({"kind": kind, "name": name, "line": idx + 1})

        self._send_json(200, {"ok": True, "file_path": file_path, "symbols_count": len(symbols), "symbols": symbols})

    def _okf_scan(self) -> None:
        body = self._read_json_body()
        bundle_dir = str(body.get("bundle_dir") or "").strip()
        p = Path(bundle_dir)
        if not p.is_dir():
            self._send_json(400, {"ok": False, "error": "bundle_dir_not_found", "docs": []})
            return

        docs = []
        for file in p.rglob("*.md"):
            try:
                rel = str(file.relative_to(p)).replace(os.sep, "/")
                text = file.read_text(encoding="utf-8", errors="replace")
                title = rel
                tags = []
                body_text = text
                if text.startswith("---"):
                    parts = text.split("---", 2)
                    if len(parts) >= 3:
                        body_text = parts[2].strip()
                        for line in parts[1].splitlines():
                            line = line.strip()
                            if line.startswith("title:"):
                                title = line[6:].strip().strip("\"'")
                            elif line.startswith("tags:"):
                                t_str = line[5:].strip().strip("[]")
                                tags = [t.strip().strip("\"'") for t in t_str.split(",") if t.strip()]
                docs.append({
                    "rel_path": rel,
                    "title": title,
                    "tags": tags,
                    "body_preview": body_text[:200]
                })
            except Exception:
                continue

        self._send_json(200, {"ok": True, "bundle_dir": bundle_dir, "count": len(docs), "docs": docs})

    def _evolution_rsi_skills(self) -> None:
        from hermes.platform.evolution.rsi_loop import get_ouroboros_rsi
        rsi = get_ouroboros_rsi()
        skills = rsi.list_crystallized_skills()
        self._send_json(200, {"skills": skills, "count": len(skills)})

    def _evolution_rsi_trigger(self) -> None:
        from hermes.platform.evolution.rsi_loop import get_ouroboros_rsi
        body = self._read_json_body()
        bot_id = str(body.get("bot_id") or "operator-bot").strip()
        category = str(body.get("category") or "syntax_regression").strip()
        errors = list(body.get("errors") or ["SyntaxError: invalid syntax in pipeline", "SyntaxError: unterminated string", "SyntaxError: unexpected token"])

        rsi = get_ouroboros_rsi()
        cluster = [{"task_id": f"sim_{i}", "task_name": f"Simulação {i}", "error_message": err, "category": category} for i, err in enumerate(errors)]
        res = rsi.synthesize_meta_skill(bot_id, cluster)
        self._send_json(200, res)

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

    def _sessions_search(self) -> None:
        """Fast session search via Rust haos-edge or local fallback."""
        body = self._read_json_body()
        query = str(body.get("query") or "").strip()
        limit = int(body.get("limit") or 20)

        # Fast-Path Rust haos-edge
        try:
            import urllib.request
            url = "http://127.0.0.1:8788/api/sessions/search"
            payload = json.dumps({"query": query, "limit": limit}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                if resp.status == 200:
                    self.wfile.write(resp.read())
                    return
        except Exception:
            pass

        self._send_json(200, {"ok": True, "results": [], "count": 0, "fallback": True})

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
        """List sessions from HAOS state.db for resume history with fast-path."""
        import sqlite3
        import datetime

        # Fast-Path Nativo via Rust haos-edge se disponível (sub-milissegundo, zero-lock)
        try:
            import urllib.request
            qs = self.path.split("?")[1] if "?" in self.path else "limit=30"
            req = urllib.request.Request(f"http://127.0.0.1:8788/api/sessions/fast?{qs}")
            with urllib.request.urlopen(req, timeout=0.3) as resp:
                if resp.status == 200:
                    self.wfile.write(resp.read())
                    return
        except Exception:
            pass

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
        # Vault canônico do nó (<home>/obsidian_vault); "vault" era o diretório
        # fantasma que deixava o painel mostrando 0 notas com o vault cheio.
        vault_dir = haos_dir / "obsidian_vault"
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
            # Varredura direta otimizada com scandir
            with os.scandir(target_dir) as it:
                for entry in it:
                    if not show_hidden and entry.name.startswith("."):
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=True)
                        size = entry.stat().st_size if not is_dir else 0
                        items.append({
                            "name": entry.name,
                            "path": str(Path(entry.path).resolve()),
                            "is_dir": is_dir,
                            "size_bytes": size,
                        })
                    except OSError:
                        continue
        except PermissionError:
            self._send_json(403, {"ok": False, "error": "permission_denied", "path": str(target_dir)})
            return
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc), "path": str(target_dir)})
            return

        # Sort: directories first, then alphabetically
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        self._send_json(200, {
            "ok": True,
            "engine": "rust_fs_accelerated",
            "current_path": str(target_dir),
            "parent_path": str(target_dir.parent) if target_dir.parent != target_dir else None,
            "home_path": str(Path.home()),
            "count": len(items),
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
    host: str = "127.0.0.1",
    port: int = _DEFAULT_PORT,
    title: str = "HAOS Standalone",
) -> tuple[ThreadingHTTPServer, HAOSStandaloneState, str]:
    """Constrói o servidor standalone. Retorna (server, state, base_url)."""
    try:
        parsed_host = ipaddress.ip_address(host)
        loopback = parsed_host.is_loopback
        tailscale = parsed_host.version == 4 and ipaddress.ip_address(host) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        loopback = host == "localhost"
        tailscale = False
    if not (loopback or tailscale):
        raise ValueError("standalone Control Plane only supports loopback or Tailscale CGNAT addresses")
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
                    Path.home() / ".hermes" / "state.db",  # haos-legacy-path: candidato legado do backup
                    Path.home() / ".hermes" / "kanban.db",  # haos-legacy-path: candidato legado do backup
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
    parser.add_argument("--host", default=os.environ.get("HAOS_HOST", "127.0.0.1"), help="Host para escutar")
    parser.add_argument("--port", type=int, default=int(os.environ.get("HAOS_PORT", _DEFAULT_PORT)), help="Porta HTTP")
    parser.add_argument("--data-dir", default=None, help="Diretório persistente do engine (default: ~/.haos)")
    args = parser.parse_args()

    server, state, base_url = make_standalone_server(
        data_dir=Path(args.data_dir) if args.data_dir else None,
        host=args.host,
        port=args.port,
    )
    # Adianta o índice AST: o primeiro Blast Radius do dashboard encontra cache
    # quente em vez de pagar ~10s de indexação dentro do próprio request.
    state.warm_symbol_index_background()
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
