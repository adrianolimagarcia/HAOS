"""C — UI/Control Plane (Fase 3): ``haos.ui.dashboard`` — estado observável em
HTML mínimo, derivado das views (AionUI/Studio).

Pré-requisito para o shell oficial: o estado que o dashboard renderiza é o
mesmo JSON das views; o HTML aqui é só o transport do demo (sem texto fixo).
Renderização é pura sobre as views — os pontos de dado reais fluem do Kanban
canônico/EventStore; nada de métricas fabricadas.
"""

from html import escape
from typing import Any, Dict

from hermes.platform.ui.views import (
    approvals_view, memory_graph_view, taskboard_view,
    concurrency_view, critical_path_view,
)


def render_dashboard(stats, *, title: str = "HAOS Control Plane") -> str:
    """Renderiza as views em HTML responsivo e estilizado (demo server). Sempre deriva das
    views reais: se o store não está disponível, cada bloco mostra vazio."""
    board = taskboard_view(stats)
    approvals = approvals_view(stats)
    graph = memory_graph_view(stats)
    concurrency = concurrency_view(stats)
    cpm = critical_path_view(stats)

    css = """
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #c9d1d9;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --success: #2ea043;
      --warning: #d29922;
      --danger: #f85149;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 24px;
      line-height: 1.5;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 16px;
      margin-bottom: 24px;
      border-bottom: 1px solid var(--border);
    }
    h1 { font-size: 1.5rem; color: #fff; display: flex; align-items: center; gap: 8px; }
    .badge {
      font-size: 0.75rem;
      padding: 2px 8px;
      border-radius: 12px;
      background: var(--border);
      color: var(--text);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 20px;
      margin-bottom: 24px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 18px;
    }
    .card h2 {
      font-size: 1.1rem;
      color: #fff;
      margin-bottom: 14px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 8px;
      display: flex;
      justify-content: space-between;
    }
    .stat-row {
      display: flex;
      justify-content: space-between;
      padding: 6px 0;
      border-bottom: 1px solid #21262d;
      font-size: 0.9rem;
    }
    .stat-row:last-child { border-bottom: none; }
    .stat-val { font-family: monospace; font-weight: 600; }
    .val-ok { color: var(--success); }
    .val-accent { color: var(--accent); }
    .val-warn { color: var(--warning); }
    .task-list { list-style: none; margin-top: 8px; }
    .task-item {
      padding: 8px 10px;
      margin-bottom: 6px;
      background: #0d1117;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 0.85rem;
    }
    .links-bar {
      margin-top: 24px;
      padding: 12px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      font-size: 0.85rem;
      display: flex;
      gap: 16px;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    """

    parts = [
        "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'>",
        f"<title>{escape(title)}</title>",
        f"<style>{css}</style></head><body>",
        "<header>",
        f"<h1>⚡ {escape(title)} <span class='badge'>v1.2</span></h1>",
        "<div><span class='badge' style='background:#238636;color:#fff;'>ONLINE</span></div>",
        "</header>",
        "<div class='grid'>",
    ]

    # Card 1: Taskboard & Kanban
    parts.append("<div class='card'><h2>📋 Taskboard <span>Total: " + str(board['total']) + "</span></h2>")
    if board["columns"]:
        for col in board["columns"]:
            parts.append(f"<div class='stat-row'><span>{escape(str(col['status']).upper())}</span><span class='stat-val val-accent'>{col['count']}</span></div>")
    else:
        parts.append("<p style='color:var(--text-muted);font-size:0.85rem;'>Nenhuma coluna com tarefas ativas.</p>")
    
    if board["recent"]:
        parts.append("<div style='margin-top:12px;font-size:0.8rem;color:var(--text-muted);font-weight:600;'>TAREFAS RECENTES:</div><ul class='task-list'>")
        for item in board["recent"][:5]:
            tid = escape(str(item.get("id", "")))
            title_task = escape(str(item.get("title", tid)))
            st = escape(str(item.get("status", "")))
            parts.append(f"<li class='task-item'><strong>{tid}</strong>: {title_task} <span class='badge' style='float:right;'>{st}</span></li>")
        parts.append("</ul>")
    parts.append("</div>")

    # Card 2: Concurrency & Backpressure
    parts.append("<div class='card'><h2>🛡️ ConcurrencyGuard & Backpressure</h2>")
    parts.append(f"<div class='stat-row'><span>Workers Ativos / Teto Global</span><span class='stat-val'>{concurrency['active_global']} / {concurrency['max_global']}</span></div>")
    parts.append(f"<div class='stat-row'><span>Slots Disponíveis</span><span class='stat-val val-ok'>{concurrency['available_global']}</span></div>")
    if concurrency["providers"]:
        parts.append("<div style='margin-top:10px;font-size:0.8rem;color:var(--text-muted);font-weight:600;'>LIMITES POR PROVEDOR:</div>")
        for p_name, p_data in concurrency["providers"].items():
            parts.append(f"<div class='stat-row'><span>{escape(p_name)}</span><span class='stat-val'>{p_data['active']} / {p_data['limit']}</span></div>")
    parts.append("</div>")

    # Card 3: Critical Path Method (CPM & PIP)
    parts.append("<div class='card'><h2>📐 Scheduler CPM & PIP</h2>")
    parts.append(f"<div class='stat-row'><span>Tarefas Avaliadas no Grafo</span><span class='stat-val'>{cpm['total_tasks_evaluated']}</span></div>")
    cp_nodes = ", ".join(cpm["critical_path_ids"]) if cpm["critical_path_ids"] else "Nenhum nó crítico"
    parts.append(f"<div class='stat-row'><span>Nós no Caminho Crítico</span><span class='stat-val val-warn'>{escape(cp_nodes)}</span></div>")
    if cpm["inherited_priorities"]:
        parts.append("<div style='margin-top:10px;font-size:0.8rem;color:var(--text-muted);font-weight:600;'>PRIORIDADES HERDADAS (PIP):</div>")
        for tid, prio in sorted(cpm["inherited_priorities"].items(), key=lambda x: x[1], reverse=True)[:4]:
            parts.append(f"<div class='stat-row'><span>{escape(tid)}</span><span class='stat-val val-accent'>{prio}</span></div>")
    parts.append("</div>")

    # Card 4: Approvals & Memory Graph
    parts.append("<div class='card'><h2>🧠 Memória & Aprovações</h2>")
    decision_text = "decision=ok" if approvals["decision"] else f"pending={len(approvals['pending'])}"
    decision_class = "val-ok" if approvals["decision"] else "val-warn"
    parts.append(f"<div class='stat-row'><span>Status de Aprovação</span><span class='stat-val {decision_class}'>{decision_text}</span></div>")
    parts.append(f"<div class='stat-row'><span>Trace Edges / Correlation</span><span class='stat-val'>{graph['trace_edges']} / {graph['correlation_edges']}</span></div>")
    if graph["nodes"]:
        parts.append("<div style='margin-top:10px;font-size:0.8rem;color:var(--text-muted);font-weight:600;'>ENTIDADES CONHECIDAS:</div>")
        for node in graph["nodes"]:
            parts.append(f"<div class='stat-row'><span>{escape(str(node['type']))}</span><span class='stat-val'>{node['count']}</span></div>")
    parts.append("</div>")

    parts.append("</div>")  # Fecha .grid

    # Links bar
    parts.append("<div class='links-bar'>")
    parts.append("<span>🔗 <strong>Acessos Rápidos:</strong></span>")
    parts.append("<a href='/api/state' target='_blank'>JSON Payload (/api/state)</a>")
    parts.append("<a href='http://127.0.0.1:9119/haos' target='_blank'>Dashboard Oficial Hermes (/haos na porta 9119)</a>")
    parts.append("</div>")

    parts.append("</body></html>")
    return "".join(parts)


def dashboard_payload(stats) -> Dict[str, Any]:
    """Payload JSON canônico das views — o que uma rota de API do shell
    oficial serviria (o HTML demo é derivado disto)."""
    return {
        "taskboard": taskboard_view(stats),
        "approvals": approvals_view(stats),
        "memory_graph": memory_graph_view(stats),
        "concurrency": concurrency_view(stats),
        "critical_path": critical_path_view(stats),
    }
