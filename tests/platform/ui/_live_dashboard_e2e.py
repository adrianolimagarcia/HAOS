"""Frente 2 — E2E AO VIVO do plugin /haos no Web Dashboard OFICIAL do Hermes.

Validação visual ao vivo (nível HTTP real, processo oficial): roda o
``hermes_cli.web_server`` DE VERDADE (descoberta + gates + mount + assets +
SPA) com o bundle HAOS instalado como user plugin em HERMES_HOME temporário e
``plugins.enabled: [haos]`` no config.yaml. Depois injeta o wiring dos stores
canônicos via ``configure_stats`` no módulo que o mount oficial registrou
(``hermes_dashboard_plugin_haos`` em sys.modules — o mesmo passo de "montagem
final do shell" documentado em INTEGRATIONS §5) e verifica por HTTP:

  * discovery lista haos como user plugin (``/api/dashboard/plugins``);
  * assets do bundle são servidos (``/dashboard-plugins/haos/dist/index.js`` e
    ``style.css``) com content-type certo;
  * rotas do backend montadas sob ``/api/plugins/haos/`` respondem com stores
    REAIS seedados (taskboard com card, evolution pending, grants reais);
  * POST /api/plugins/haos/dispatch executa um card READY por HTTP real — prova
    o fix delta 48 (conexão por thread no KanbanAdapter) sob o threadpool do
    FastAPI;
  * a SPA oficial (web_dist construída) é servida no ``/`` e a rota cliente
    ``/haos`` cai no index.html (client-side routing do shell).

Nunca toca ~/.hermes; nunca edita upstream. Roda em processo limpo:

  HERMES_WEB_DIST=$PWD/hermes_cli/web_dist uv run --no-project --with fastapi \
    --with httpx --with uvicorn --with pyyaml --with python-multipart \
    --with aiofiles --with python-dotenv --python 3.14 \
    python tests/platform/ui/_live_dashboard_e2e.py

Exit code 0 = tudo verde; qualquer assert falho = exit 1 (receipt honesto).
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_PLUGIN_SRC = _REPO / "hermes/platform/ui/dashboard_plugin"


def _step(name: str) -> None:
    print(f"\n== {name} ==")


def main() -> int:
    failures: list[str] = []
    root = Path(tempfile.mkdtemp(prefix="haos-live-dashboard-"))
    sys.path.insert(0, str(_REPO))  # importar hermes.platform/hermes_cli do repo

    # 1) Ambiente isolado: HERMES_HOME/KANBAN_HOME temporários.
    os.environ["HERMES_HOME"] = str(root / "hermes-home")
    os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
    os.environ["HERMES_DASHBOARD_SESSION_TOKEN"] = "e2e-session-token-live"
    home = Path(os.environ["HERMES_HOME"])
    kanban_home = Path(os.environ["HERMES_KANBAN_HOME"])
    (home / "plugins").mkdir(parents=True)
    (kanban_home).mkdir(parents=True)

    # 2) Instala o bundle como user plugin (copiado, como um operador faria).
    plugin_dst = home / "plugins" / "haos" / "dashboard"
    shutil.copytree(_PLUGIN_SRC, plugin_dst)

    # 3) config.yaml com plugins.enabled: [haos] (gate do mount oficial).
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - haos\n", encoding="utf-8"
    )

    # 4) Semeia os stores canônicos REAIS (Kanban no DB canônico default +
    #    EventStore + proposta evolution + grant pendente).
    os.environ.setdefault("HERMES_WEB_DIST", str(_REPO / "hermes_cli" / "web_dist"))

    from hermes.platform.auth.vault import SecretBroker
    from hermes.platform.evolution.ledger import EvolutionLedger
    from hermes.platform.observability.event_store import EventStore
    from hermes.platform.tasks.kanban_adapter import KanbanAdapter
    from hermes.platform.tasks.spec import TaskSpec
    from hermes.platform.ui.stats import DashboardStats

    adapter = KanbanAdapter()  # DB canônico: <HERMES_KANBAN_HOME>/kanban.db
    events_db = kanban_home / "events.db"
    store = EventStore(str(events_db))

    tid = adapter.save_task(
        TaskSpec(id="T-LIVE", title="Live E2E card", goal="rodar no dashboard",
                 workspace_type="scratch", required_capabilities=["git"]),
        status="READY",
    )
    ledger = EvolutionLedger(store)
    pid = ledger.submit({"target": "live", "current_profile": "a",
                         "proposed_profile": "b", "mode": "PROPOSAL_ONLY",
                         "rationale": "e2e ao vivo"})
    broker = SecretBroker()
    broker.store_secret("svc:live", "tok-live")
    broker.grant("ci", "svc:live", policy="requires_approval", requester="e2e")

    # 5) Importa o web_server OFICIAL com este HERMES_HOME (descoberta + gates +
    #    mount do plugin acontecem no import — mesmo caminho do processo real).
    _step("Import hermes_cli.web_server (descoberta + mount oficiais)")
    import hermes_cli.web_server  # noqa: F401
    import sys as _sys

    mounted = _sys.modules.get("hermes_dashboard_plugin_haos")
    if mounted is None:
        failures.append("plugin_api NÃO foi montado em sys.modules (gate falhou?)")
    else:
        # Wiring dos stores canônicos no módulo que o mount registrou — o mesmo
        # passo de montagem final do shell (configure_stats).
        mounted.configure_stats(DashboardStats(kanban=adapter,
                                               event_store=store))
        print(f"  mounted module OK; router={getattr(mounted, 'router', None) is not None}")

    from starlette.testclient import TestClient

    app = hermes_cli.web_server.app
    headers = {"X-Hermes-Session-Token": "e2e-session-token-live"}
    client = TestClient(app, raise_server_exceptions=False)

    # 6) Verificações por HTTP real.
    def check(name: str, cond: bool, detail: str = "") -> None:
        print(("  PASS " if cond else "  FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    _step("Discovery: /api/dashboard/plugins contém haos (user plugin)")
    r = client.get("/api/dashboard/plugins")
    check("GET /api/dashboard/plugins == 200", r.status_code == 200, str(r.status_code))
    plugins = r.json() if r.status_code == 200 else []
    haos = next((p for p in plugins if p.get("name") == "haos"), None)
    check("haos presente na lista", haos is not None)
    if haos:
        check("haos.source == user", haos.get("source") == "user", str(haos.get("source")))
        check("haos entry/css declarados", bool(haos.get("entry")) and bool(haos.get("css")))

    _step("Assets: /dashboard-plugins/haos/dist/index.js + style.css servidos")
    js = client.get("/dashboard-plugins/haos/dist/index.js")
    check("entry js == 200", js.status_code == 200, str(js.status_code))
    check("content-type javascript", "javascript" in (js.headers.get("content-type") or ""),
          js.headers.get("content-type", ""))
    css = client.get("/dashboard-plugins/haos/dist/style.css")
    check("css == 200", css.status_code == 200, str(css.status_code))
    check("content-type css", "text/css" in (css.headers.get("content-type") or ""),
          css.headers.get("content-type", ""))

    _step("Backend montado: /api/plugins/haos/state com stores REAIS")
    st = client.get("/api/plugins/haos/state", headers=headers)
    check("GET state == 200", st.status_code == 200, f"{st.status_code} {st.text[:160]}")
    if st.status_code == 200:
        payload = st.json()
        tb = payload.get("taskboard", {})
        check("taskboard.total >= 1 (card READY seedado)", tb.get("total", 0) >= 1,
              f"total={tb.get('total')}")
        check("evolution_pending == 1", len(payload.get("evolution_pending", [])) == 1,
              str(len(payload.get("evolution_pending", []))))
        # Delta 49: grants_pending reflete o grant requires_approval seedado.
        check("grants_pending == 1 (grant requires_approval seedado)",
              len(payload.get("grants_pending", [])) == 1,
              str(payload.get("grants_pending")))

    _step("Action ao vivo: POST /api/plugins/haos/dispatch roda card READY")
    disp = client.post("/api/plugins/haos/dispatch", json={}, headers=headers)
    check("POST dispatch == 200", disp.status_code == 200, f"{disp.status_code} {disp.text[:160]}")
    if disp.status_code == 200:
        check("executed contém o card seedado", disp.json().get("executed") == [tid],
              str(disp.json()))
    check("card virou done (leitura no adapter)", (adapter.get_task(tid) or {}).get("status") == "done",
          str((adapter.get_task(tid) or {}).get("status")))

    _step("Evolution + grants ao vivo por HTTP")
    dec = client.post("/api/plugins/haos/evolution/decide", json={
        "proposal_id": pid, "verdict": "approved",
        "approver": "operator-live", "rationale": "ok"}, headers=headers)
    check("POST evolution/decide == 200", dec.status_code == 200, f"{dec.status_code} {dec.text[:160]}")
    ap = client.post("/api/plugins/haos/grants/approve", json={
        "scope": "ci", "credential_ref": "svc:live", "approver": "operator-live"},
        headers=headers)
    check("POST grants/approve == 200", ap.status_code == 200, f"{ap.status_code} {ap.text[:160]}")
    check("grant aprovado de verdade", broker.check_grant("ci", "svc:live"))
    rv = client.post("/api/plugins/haos/grants/revoke", json={
        "scope": "ci", "credential_ref": "svc:live"}, headers=headers)
    check("POST grants/revoke == 200", rv.status_code == 200, f"{rv.status_code} {rv.text[:160]}")

    _step("Delta 49: grants_pending no state + rota /acp/plan ao vivo")
    st49 = client.get("/api/plugins/haos/state", headers=headers)
    if st49.status_code == 200:
        # O grant foi aprovado e revogado acima -> nada pendente (derivado real).
        check("grants_pending vazio após approve+revoke",
              st49.json().get("grants_pending") == [], str(st49.json().get("grants_pending")))
    # Bridge ACP de planejamento: wiring do comando no módulo montado + peer
    # scriptado real (mesmo mock hermético dos testes de protocolo).
    if mounted is not None:
        mock_acp = _REPO / "tests/platform/protocols/_mock_acp_server.py"
        mounted.configure_acp([sys.executable, str(mock_acp)])
        plan = client.post("/api/plugins/haos/acp/plan", json={
            "instruction": "planeje a partir do estado atual"}, headers=headers)
        check("POST /acp/plan == 200", plan.status_code == 200,
              f"{plan.status_code} {plan.text[:160]}")
        if plan.status_code == 200:
            check("sessão ACP real (mock peer) devolve identidade",
                  plan.json().get("agent", {}).get("name") == "mock-acp-agent",
                  str(plan.json()))

    _step("SPA oficial: / serve o index.html do web_dist; /haos cai no shell")
    idx = client.get("/")
    check("GET / == 200 (index.html)", idx.status_code == 200, str(idx.status_code))
    if idx.status_code == 200:
        check("index.html é o SPA oficial",
              "<!doctype html" in idx.text.lower() or "<html" in idx.text.lower())
    spa = client.get("/haos")
    check("GET /haos cai no index.html (client-side routing)", spa.status_code == 200,
          str(spa.status_code))

    adapter.close()
    shutil.rmtree(root, ignore_errors=True)

    print("\n" + ("=" * 60))
    if failures:
        print(f"FALHAS ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("LIVE DASHBOARD E2E: TUDO VERDE (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
