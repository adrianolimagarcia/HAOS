"""Unidades systemd que o ``install_haos.sh`` provisiona — só a lacuna real.

O gateway já tem gerador canônico e testado no repo
(``hermes_cli.gateway.generate_systemd_unit`` + ``systemd_install``, coberto por
``tests/hermes_cli/test_gateway_service.py``), e o distro embute as suas próprias
unidades em ``distro/haos-linux/config/includes.chroot/etc/systemd/system/`` para
o layout ``/opt/haos`` do ISO. O **controlplane** (``scripts/serve_controlplane.py``)
não tinha gerador nenhum: o instalador o subia com ``nohup`` e um PID file, o que
funciona até o primeiro reboot. Este módulo cobre apenas essa lacuna — não
reimplementa nada do gateway.

Detalhe que o layout antigo exigia e este não: a unidade feita à mão na máquina
migrada precisava de ``PYTHONPATH=<install dir>`` porque ``hermes`` é namespace
package e ficava fora do ``packages.find`` (corrigido no commit ``fa50c15``). Com
aquela correção a venv instalada importa ``hermes.*`` sozinha, e este script não
importa ``scripts.*``, então não há PYTHONPATH aqui.

CLI (é assim que o instalador chama):

    python scripts/haos_services.py install \\
        --install-dir /usr/local/lib/haos-agent \\
        --haos-home "$HOME/.haos" \\
        --python /usr/local/lib/haos-agent/venv/bin/python [--user] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_NAME = "haos-controlplane.service"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8788
# PATH enxuto: a venv primeiro (o wrapper `haos` do mesmo diretório), depois o
# sistema. Sem ``node``/``node_modules`` de outras árvores — a unidade que este
# módulo substitui carregava PATH de uma instalação que não existe mais.
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def default_unit_dir(system: bool, *, home: Path | None = None) -> Path:
    """Diretório de unidades do escopo pedido (system vs. usuário)."""
    if system:
        return Path("/etc/systemd/system")
    return (home or Path.home()) / ".config" / "systemd" / "user"


def render_controlplane_unit(
    *,
    install_dir: str | Path,
    haos_home: str | Path,
    python: str | Path,
    run_as_user: str | None = None,
    system: bool = True,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    user_home: str | Path | None = None,
) -> str:
    """Monta a unidade do controlplane para uma instalação em ``install_dir``.

    Todos os caminhos vêm de argumento: a unidade vale tanto para o layout root
    (``/usr/local/lib/haos-agent`` + ``/root/.haos``) quanto para o de usuário
    (``~/.local/share/haos-agent`` + ``~/.haos``).
    """
    install_dir = str(install_dir)
    haos_home = str(haos_home)
    python = str(python)
    venv_bin = str(Path(python).parent)

    identity = f"User={run_as_user}\n" if (system and run_as_user) else ""
    home_line = f'Environment="HOME={user_home or (Path.home() if not system else "/root")}"\n'
    wanted_by = "multi-user.target" if system else "default.target"

    return (
        "[Unit]\n"
        "Description=HAOS Multi-Agent Control Plane\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"{identity}"
        f"WorkingDirectory={install_dir}\n"
        f"{home_line}"
        f'Environment="HAOS_HOME={haos_home}"\n'
        f'Environment="HERMES_HOME={haos_home}"\n'
        f'Environment="HAOS_DATA_DIR={haos_home}"\n'
        f'Environment="HAOS_HOST={host}"\n'
        f'Environment="HAOS_PORT={port}"\n'
        f'Environment="PATH={venv_bin}:{SYSTEM_PATH}"\n'
        f"ExecStart={python} {install_dir}/scripts/serve_controlplane.py\n"
        "Restart=always\n"
        "RestartSec=3\n"
        "KillMode=mixed\n"
        "TimeoutStopSec=5\n"
        "\n"
        f"[Install]\n"
        f"WantedBy={wanted_by}\n"
    )


def write_unit(content: str, path: Path) -> None:
    """Escreve a unidade de forma atômica, com a permissão que o systemd espera."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)


def systemctl_available(system: bool) -> bool:
    """``systemctl`` existe e responde no escopo pedido (systemd pode não estar)."""
    if shutil.which("systemctl") is None:
        return False
    flag = [] if system else ["--user"]
    try:
        done = subprocess.run(
            ["systemctl", *flag, "is-system-running"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # "running", "degraded", "starting" respondem; erro de D-Bus/ausência não.
    return done.stdout.strip() in {"running", "degraded", "starting", "maintenance"}


def install_controlplane_unit(
    *,
    install_dir: str | Path,
    haos_home: str | Path,
    python: str | Path,
    system: bool = True,
    run_as_user: str | None = None,
    enable: bool = True,
    start: bool = True,
    unit_dir: Path | None = None,
    dry_run: bool = False,
) -> Path:
    """Instala a unidade e (por padrão) habilita no boot e sobe agora."""
    content = render_controlplane_unit(
        install_dir=install_dir,
        haos_home=haos_home,
        python=python,
        run_as_user=run_as_user,
        system=system,
    )
    path = (unit_dir or default_unit_dir(system)) / UNIT_NAME

    if dry_run:
        print(f"[dry-run] escreveria {path}:\n")
        print(content)
        return path

    write_unit(content, path)
    flag = [] if system else ["--user"]
    subprocess.run(["systemctl", *flag, "daemon-reload"], check=False)
    if enable:
        subprocess.run(["systemctl", *flag, "enable", UNIT_NAME], check=False)
    if start:
        subprocess.run(["systemctl", *flag, "restart", UNIT_NAME], check=False)
    return path


def _add_scope_flags(parser: argparse.ArgumentParser) -> None:
    """``--system``/``--user`` explícitos; sem flag, o escopo sai do euid.

    Sem escolha explícita o padrão é o único sensato (root → sistema, resto →
    usuário), mas quem chama de script passa a flag sempre — foi assim que uma
    instalação de usuário quase tentou escrever em ``/etc/systemd/system``.
    """
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--system", dest="system", action="store_true", default=None,
                       help="escopo de sistema (/etc/systemd/system)")
    scope.add_argument("--user", dest="system", action="store_false",
                       help="escopo de usuário (~/.config/systemd/user)")


def _resolve_system(system: bool | None) -> bool:
    """Escopo default quando ninguém passou flag: sistema só como root.

    ``os.geteuid`` não existe no Windows (que também não tem systemd), então o
    ``hasattr`` é um gate de verdade, não enfeite — sem ele o módulo levanta
    AttributeError lá. O chamador shell sempre passa ``--system``/``--user``;
    este default existe para quem roda o script à mão.
    """
    if system is not None:
        return system
    if not hasattr(os, "geteuid"):
        return False
    return os.geteuid() == 0  # windows-footgun: ok (guardado por hasattr acima)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provisiona a unidade do controlplane HAOS.")
    sub = parser.add_subparsers(dest="action", required=True)

    install = sub.add_parser("install", help="Escreve, habilita e sobe a unidade")
    install.add_argument("--install-dir", required=True)
    install.add_argument("--haos-home", required=True)
    install.add_argument("--python", required=True, help="python da venv do HAOS")
    install.add_argument("--run-as-user", default=None)
    _add_scope_flags(install)
    install.add_argument("--no-enable", dest="enable", action="store_false", default=True)
    install.add_argument("--no-start", dest="start", action="store_false", default=True)
    install.add_argument("--dry-run", action="store_true")

    render = sub.add_parser("render", help="Imprime a unidade (sem escrever nada)")
    render.add_argument("--install-dir", required=True)
    render.add_argument("--haos-home", required=True)
    render.add_argument("--python", required=True)
    render.add_argument("--run-as-user", default=None)
    _add_scope_flags(render)

    args = parser.parse_args(argv)
    system = _resolve_system(args.system)

    if args.action == "render":
        sys.stdout.write(render_controlplane_unit(
            install_dir=args.install_dir, haos_home=args.haos_home, python=args.python,
            run_as_user=args.run_as_user, system=system,
        ))
        return 0

    if not systemctl_available(system) and not args.dry_run:
        print("systemctl indisponível neste escopo — nada foi provisionado.", file=sys.stderr)
        return 1

    path = install_controlplane_unit(
        install_dir=args.install_dir, haos_home=args.haos_home, python=args.python,
        system=system, run_as_user=args.run_as_user,
        enable=args.enable, start=args.start, dry_run=args.dry_run,
    )
    print(f"unidade do controlplane: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
