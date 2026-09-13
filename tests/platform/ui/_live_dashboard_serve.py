"""Operador — Sobe o Web Dashboard OFICIAL com o plugin /haos para validação
visual no browser (delta 49/50 — passo de operador final).

Igual ao E2E ao vivo (``_live_dashboard_e2e.py``: HERMES_HOME/KANBAN_HOME
temporários, bundle instalado como user plugin + ``plugins.enabled: [haos]``,
wiring ``configure_stats`` no módulo montado), mas em vez de TestClient ele
chama ``start_server`` DE VERDADE e fica servindo até Ctrl+C — o operador abre
a URL no browser e CLICA nos botões da aba /haos.

Autenticação (sem OAuth de sistema): o auth gate do Web Dashboard exige um
provider para bind fora de loopback (hardening upstream Jun/2026). A via
escolhida é o provider BUNDLED de username/senha (``dashboard_auth/basic`` —
auth local, sem OAuth/Nous), configurado via config.yaml + ``discover_plugins``
(exatamente como ``hermes dashboard`` faria no modo interativo). O OAuth no
sistema é para os PROVIDERS de modelo, não para o login do dashboard.

Dados seedados para a validação visual:
  * 1 card READY (botão "Dispatch ready" executa o dispatcher canônico);
  * 1 proposta de evolution pending ("Approve"/"Reject");
  * 1 grant ``requires_approval`` pendente ("Approve"/"Revoke");
  * conector ACP CONFIGURADO com o mock peer hermético do K5 — "Plan over ACP"
    abre uma sessão real (start -> session/new -> prompt) e devolve o resultado
    na view.

Uso (na raiz do repo, com web_dist construída):
  HERMES_WEB_DIST=$PWD/hermes_cli/web_dist uv run --no-project \
    --with fastapi --with httpx --with uvicorn --with pyyaml \
    --with python-multipart --with aiofiles --with python-dotenv \
    --python 3.14 python tests/platform/ui/_live_dashboard_serve.py \
    [--port 9121] [--host 0.0.0.0] [--user admin] [--password <senha>]

Default ``--host 0.0.0.0`` expõe na LAN (o auth de username/senha protege);
``--host 127.0.0.1`` fica local (sem login, token injetado). Credenciais
default admin/``haos-visual-2025`` (imprima-as no stdout). Nunca toca
~/.hermes. Exit 0 em Ctrl+C; exit 1 em erro de setup (receipt honesto).
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_PLUGIN_SRC = _REPO / "hermes/platform/ui/dashboard_plugin"
_MOCK_ACP = _REPO / "tests/platform/protocols/_mock_acp_server.py"
_DEFAULT_PASSWORD = "haos-visual-2025"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9121)
    parser.add_argument("--host", default="0.0.0.0",
                        help="interface (default 0.0.0.0 = LAN; use 127.0.0.1 p/ local)")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default=_DEFAULT_PASSWORD)
    args = parser.parse_args()

    # Armazena em diretório persistente e fixo em vez de mkdtemp descartável
    # a cada reinício, para preservar chaves de API (.env), configurações e tarefas.
    root = Path("/tmp/haos-live-serve-persistent")
    root.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(_REPO))

    os.environ["HERMES_HOME"] = str(root / "hermes-home")
    os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
    os.environ["HERMES_DASHBOARD_SESSION_TOKEN"] = "haos-live-serve-token"
    os.environ.setdefault("HERMES_WEB_DIST", str(_REPO / "hermes_cli" / "web_dist"))
    home = Path(os.environ["HERMES_HOME"])
    kanban_home = Path(os.environ["HERMES_KANBAN_HOME"])
    (home / "plugins").mkdir(parents=True, exist_ok=True)
    (kanban_home).mkdir(parents=True, exist_ok=True)

    # 1) Bundle como user plugin + config.yaml com gate de mount + auth local.
    target_plugin = home / "plugins" / "haos" / "dashboard"
    if target_plugin.exists():
        shutil.rmtree(target_plugin)
    shutil.copytree(_PLUGIN_SRC, target_plugin)
    if args.host in ("127.0.0.1", "localhost", "::1"):
        config_text = "plugins:\n  enabled:\n    - haos\n"
        login_note = "(loopback: sem login — token de sessão injetado)"
    else:
        from plugins.dashboard_auth.basic import hash_password

        pw_hash = hash_password(args.password)
        # Se já existe config.yaml, preserva chaves existentes (ex: model, provider)
        cfg_file = home / "config.yaml"
        existing_cfg = cfg_file.read_text(encoding="utf-8") if cfg_file.exists() else ""
        if "dashboard:" not in existing_cfg:
            config_text = (
                existing_cfg.strip() + "\n\n"
                "plugins:\n  enabled:\n    - haos\n"
                "dashboard:\n  basic_auth:\n"
                f"    username: {args.user}\n"
                f"    password_hash: \"{pw_hash}\"\n"
                "    secret: haos-live-serve-secret-0123456789abcdef\n"
            ).lstrip()
        else:
            config_text = existing_cfg
        login_note = f"(LAN: login {args.user} / senha fornecida — auth local, sem OAuth)"
    (home / "config.yaml").write_text(config_text, encoding="utf-8")

    # 2) Stores canônicos REAIS seedados para a validação visual.
    from hermes.platform.auth.vault import SecretBroker
    from hermes.platform.evolution.ledger import EvolutionLedger
    from hermes.platform.observability.event_store import EventStore
    from hermes.platform.tasks.kanban_adapter import KanbanAdapter
    from hermes.platform.tasks.spec import TaskSpec
    from hermes.platform.ui.stats import DashboardStats

    adapter = KanbanAdapter()
    store = EventStore(str(kanban_home / "events.db"))
    adapter.save_task(
        TaskSpec(id="T-VISUAL", title="Validar no browser", goal="clique real",
                 workspace_type="scratch", required_capabilities=["git"]),
        status="READY",
    )
    ledger = EvolutionLedger(store)
    pid = ledger.submit({"target": "visual", "current_profile": "a",
                         "proposed_profile": "b", "mode": "PROPOSAL_ONLY",
                         "rationale": "validacao visual"})
    broker = SecretBroker()
    broker.store_secret("svc:visual", "tok-visual")
    broker.grant("ci", "svc:visual", policy="requires_approval",
                 requester="operator")

    # 3) Discovery de plugins ANTES do import oficial quando o auth local é
    #    necessário (registra o provider basic no registry global). O import do
    #    web_server faz a descoberta dos DASHBOARD plugins (mount do haos).
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        import hermes_cli.plugins as _plugins_mod

        _plugins_mod.discover_plugins(force=True)

    import hermes_cli.web_server  # noqa: F401
    import sys as _sys

    mounted = _sys.modules.get("hermes_dashboard_plugin_haos")
    if mounted is None:
        print("ERRO: plugin_api não montado (gate falhou?)", file=sys.stderr)
        shutil.rmtree(root, ignore_errors=True)
        return 1
    mounted.configure_stats(DashboardStats(kanban=adapter, event_store=store))
    if _MOCK_ACP.exists():
        mounted.configure_acp([sys.executable, str(_MOCK_ACP)])

    # 4) Sobe o servidor oficial e fica servindo (bloqueia até Ctrl+C).
    urls = "http://127.0.0.1:%d" % args.port if args.host == "0.0.0.0" else \
        f"http://{args.host}:{args.port}"
    print("=" * 64)
    print("HAOS live dashboard — setup OK. Pressione Ctrl+C para parar.")
    print(f"  URL                  : {urls}  (aba /haos)")
    print(f"  Auth                 : {login_note}")
    print(f"  HERMES_HOME temp     : {home}")
    print(f"  card READY           : T-VISUAL")
    print(f"  evolution pending    : {pid}")
    print(f"  grant pending        : ci -> svc:visual (requires_approval)")
    print(f"  ACP peer             : mock hermético do K5")
    print("=" * 64, flush=True)
    from hermes_cli.web_server import start_server

    start_server(host=args.host, port=args.port, open_browser=False)
    shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nParado pelo operador.")
        sys.exit(0)
