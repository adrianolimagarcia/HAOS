#!/usr/bin/env python3
"""HAOS (Hermes Agentic OS) — Executive MOTD & System Dashboard.

Displays a rich, real-time terminal status screen with system vitals,
HAOS daemon statuses, multi-agent topology, and quick shortcuts.
"""

import datetime
import os
import platform
import shutil
import socket
import sys
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

from rich.box import ROUNDED, HEAVY, DOUBLE
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns

console = Console()

# ── Colors & Palette ────────────────────────────────────────────────────────
CYAN_BRIGHT = "#00F0FF"
TEAL = "#2DD4BF"
BLUE_NEON = "#38BDF8"
PURPLE = "#A855F7"
GREEN_OK = "#10B981"
RED_ERR = "#EF4444"
YELLOW_WARN = "#F59E0B"
GRAY_DIM = "#64748B"
GRAY_LIGHT = "#94A3B8"
WHITE = "#F8FAFC"
BORDER_COLOR = "#0284C7"

# ── ASCII Logo ─────────────────────────────────────────────────────────────
LOGO_HAOS = """[bold #00F0FF]  ██╗  ██╗ █████╗  ██████╗ ███████╗[/]
[bold #2DD4BF]  ██║  ██║██╔══██╗██╔═══██╗██╔════╝[/]
[bold #38BDF8]  ███████║███████║██║   ██║███████╗[/]
[bold #818CF8]  ██╔══██║██╔══██║██║   ██║╚════██║[/]
[bold #A855F7]  ██║  ██║██║  ██║╚██████╔╝███████║[/]
[bold #C084FC]  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝[/]"""


def make_bar(percent: float, width: int = 14) -> str:
    """Generate a colored Unicode progress bar."""
    filled = int(round((percent / 100.0) * width))
    filled = max(0, min(width, filled))
    empty = width - filled

    if percent < 60:
        color = GREEN_OK
    elif percent < 85:
        color = YELLOW_WARN
    else:
        color = RED_ERR

    bar = f"[{color}]" + "━" * filled + f"[/][{GRAY_DIM}]" + "─" * empty + "[/]"
    return f"{bar} [{color}]{percent:4.1f}%[/]"


def check_port_listening(host: str, port: int, timeout: float = 0.2) -> bool:
    """Check if a TCP port is actively listening."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def get_tailscale_ip() -> str:
    """Find Tailscale 100.x.y.z IP if active."""
    if not psutil:
        return "127.0.0.1"
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address.startswith("100."):
                return addr.address
    return "127.0.0.1"


def get_uptime_str() -> str:
    if not psutil:
        return "unknown"
    boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
    delta = datetime.datetime.now() - boot_time
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m"


def build_system_table() -> Table:
    """Build left card: Host & System Vitals."""
    table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    table.add_column("Key", style=f"bold {GRAY_LIGHT}", width=12)
    table.add_column("Val", style=WHITE)

    # OS / Kernel
    distro = "Linux"
    if Path("/etc/os-release").exists():
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                if line.startswith("PRETTY_NAME="):
                    distro = line.split("=", 1)[1].strip('"')
                    break
        except Exception:
            pass
    table.add_row("OS / Kernel", f"[{TEAL}]{distro}[/] [dim]({platform.release()})[/dim]")
    table.add_row("Uptime", f"[{WHITE}]{get_uptime_str()}[/] [dim]· host: {socket.gethostname()}[/dim]")

    if psutil:
        # CPU
        cpu_pct = psutil.cpu_percent(interval=None)
        cores = psutil.cpu_count(logical=True)
        table.add_row("CPU Load", f"{make_bar(cpu_pct)} [dim]({cores} threads)[/dim]")

        # Memory
        mem = psutil.virtual_memory()
        mem_used_gb = mem.used / (1024 ** 3)
        mem_total_gb = mem.total / (1024 ** 3)
        table.add_row("Memory", f"{make_bar(mem.percent)} [dim]{mem_used_gb:.1f}/{mem_total_gb:.1f} GB[/dim]")

        # Disk
        disk = psutil.disk_usage("/")
        disk_used_gb = disk.used / (1024 ** 3)
        disk_total_gb = disk.total / (1024 ** 3)
        table.add_row("Disk (/)", f"{make_bar(disk.percent)} [dim]{disk_used_gb:.0f}/{disk_total_gb:.0f} GB[/dim]")

        # Load avg
        if hasattr(os, "getloadavg"):
            l1, l5, l15 = os.getloadavg()
            table.add_row("Load Avg", f"[{BLUE_NEON}]{l1:.2f}[/], [{BLUE_NEON}]{l5:.2f}[/], [{BLUE_NEON}]{l15:.2f}[/]")

    return table


def build_haos_table() -> Table:
    """Build right card: HAOS Multi-Agent Status & Endpoints."""
    table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    table.add_column("Key", style=f"bold {GRAY_LIGHT}", width=15)
    table.add_column("Val", style=WHITE)

    ts_ip = get_tailscale_ip()

    # HAOS Control Plane (:8788)
    cp_active = check_port_listening("127.0.0.1", 8788)
    cp_badge = f"[{GREEN_OK}]● ONLINE[/]" if cp_active else f"[{RED_ERR}]○ OFFLINE[/]"
    table.add_row("Control Plane", f"{cp_badge}  [{CYAN_BRIGHT}]http://{ts_ip}:8788/[/]")

    # Hermes Dashboard (:9191)
    dash_active = check_port_listening("127.0.0.1", 9191)
    dash_badge = f"[{GREEN_OK}]● ONLINE[/]" if dash_active else f"[{RED_ERR}]○ OFFLINE[/]"
    table.add_row("Hermes Config", f"{dash_badge}  [{CYAN_BRIGHT}]http://{ts_ip}:9191/[/]")

    # Model & Provider
    table.add_row("Primary Model", f"[{PURPLE}]deepseek-v4-flash[/] [dim]via[/] [{TEAL}]A6API[/]")

    # Topology
    table.add_row("Topology", f"[{TEAL}]GasTown[/] [dim](Mayor · Witness · Polecat)[/dim]")

    # Storage & Integrity
    table.add_row("Data Kernel", f"[{GREEN_OK}]SQLite WAL[/] [dim](kanban.db · 30s timeout)[/dim]")
    table.add_row("Golden Tasks", f"[{GREEN_OK}]10/10 Ready[/] [dim](G001–G010 Benchmark Suite)[/dim]")

    return table


def display_motd():
    term_width = shutil.get_terminal_size((100, 30)).columns

    # 1. Header Banner
    header_table = Table(box=None, show_header=False, expand=True, padding=(0, 2))
    header_table.add_column("Logo", justify="left", width=42)
    header_table.add_column("Meta", justify="left")

    meta_text = f"""
[bold {CYAN_BRIGHT}]HAOS · Hermes Agentic Operating System[/]
[bold {WHITE}]Industrial Multi-Agent Distributed Execution Platform[/]
[dim {GRAY_LIGHT}]Fork Architecture v0.21.1 · PEP-420 Canonical Freeze (2026.9.8)[/]
[dim {TEAL}]Tailscale Active: [/][bold {TEAL}]{get_tailscale_ip()}[/]
"""
    header_table.add_row(LOGO_HAOS.strip(), meta_text.strip())

    header_panel = Panel(
        header_table,
        border_style=BORDER_COLOR,
        box=ROUNDED,
        padding=(0, 1),
    )

    # 2. Two Columns: System Vitals vs HAOS Engine
    left_panel = Panel(
        build_system_table(),
        title=f"[bold {BLUE_NEON}]🖥️  SYSTEM VITALS[/]",
        border_style=BORDER_COLOR,
        box=ROUNDED,
        padding=(0, 1),
    )

    right_panel = Panel(
        build_haos_table(),
        title=f"[bold {TEAL}]⚡ HAOS MULTI-AGENT RUNTIME[/]",
        border_style=BORDER_COLOR,
        box=ROUNDED,
        padding=(0, 1),
    )

    columns = Columns([left_panel, right_panel], expand=True, equal=True)

    # 3. Quick Action Shortcuts Footer
    shortcuts = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    shortcuts.add_column("C1", justify="center")
    shortcuts.add_column("C2", justify="center")
    shortcuts.add_column("C3", justify="center")
    shortcuts.add_column("C4", justify="center")

    shortcuts.add_row(
        f"[bold {CYAN_BRIGHT}]haos[/] [dim]Interactive Agent[/]",
        f"[bold {TEAL}]haos status[/] [dim]Full Diagnostics[/]",
        f"[bold {PURPLE}]haos-controlplane[/] [dim]Start All Services[/]",
        f"[bold {WHITE}]haos --help[/] [dim]Command Palette[/]",
    )

    footer_panel = Panel(
        shortcuts,
        border_style=GRAY_DIM,
        box=ROUNDED,
        padding=(0, 1),
    )

    console.print()
    console.print(header_panel)
    console.print(columns)
    console.print(footer_panel)
    console.print()


if __name__ == "__main__":
    display_motd()
