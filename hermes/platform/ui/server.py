"""C — UI/Control Plane (Fase 3): servidor demo do estado observável.

Substitui o HTML fixo do scaffold por renderização DERIVADA das views
(AionUI/Studio): a mesma ``DashboardStats`` serve o HTML demo e o payload
JSON que o shell oficial consumiria (padrão "estado observável + transporte",
sem framework embutido — decisão INTEGRATIONS §7). Tudo stdlib; store
injetável (nenhum global de /tmp aqui).
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

from hermes.platform.ui.dashboard import dashboard_payload, render_dashboard
from hermes.platform.ui.stats import DashboardStats


class UIStatsHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, stats: Optional[DashboardStats] = None,
                 title: str = "HAOS Control Plane", **kwargs):
        self._stats = stats or DashboardStats()
        self._title = title
        super().__init__(*args, **kwargs)

    def _serve(self) -> None:
        path = urlparse(self.path).path
        if self.command == "GET" and path == "/":
            html = render_dashboard(self._stats, title=self._title)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        elif self.command == "GET" and path == "/api/state":
            payload = json.dumps(dashboard_payload(self._stats)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._serve()

    def log_message(self, *args) -> None:
        pass


def make_ui_server(stats: Optional[DashboardStats] = None, *,
                   host: str = "0.0.0.0", port: int = 0,
                   title: str = "HAOS Control Plane"):
    server = ThreadingHTTPServer((host, port), lambda *a, **kw: UIStatsHandler(
        *a, stats=stats, title=title, **kw))
    base = f"http://{host}:{server.server_address[1]}"
    return server, base
