#!/usr/bin/env python3
"""jules_worker.py — CLI helper to bridge local Git repositories with Google Jules API.

Conforms to Hermes Agent Footprint Ladder (Rung 2: CLI script + skill orchestration).
Communicates with https://jules.googleapis.com/v1alpha to dispatch async coding tasks
and synchronize branches between local environment and Google Jules Cloud VM.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

JULES_API_BASE = "https://jules.googleapis.com/v1alpha"


def run_cmd(cmd: str, cwd: Optional[str] = None) -> str:
    """Executa comando shell capturando stdout e tratando erros."""
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\nstderr: {res.stderr.strip()}")
    return res.stdout.strip()


def get_github_repo_slug(cwd: Optional[str] = None) -> str:
    """Extrai owner/repo da URL remota do git (HTTPS ou SSH)."""
    url = run_cmd("git config --get remote.origin.url", cwd=cwd)
    url = url.removesuffix(".git")
    if "github.com/" in url:
        return url.split("github.com/")[1]
    elif "github.com:" in url:
        return url.split("github.com:")[1]
    raise ValueError(f"Could not parse GitHub repo slug from remote origin url: '{url}'")


def get_current_branch(cwd: Optional[str] = None) -> str:
    """Retorna o nome da branch atual."""
    return run_cmd("git rev-parse --abbrev-ref HEAD", cwd=cwd)


def get_api_key() -> str:
    """Obtém a chave JULES_API_KEY do ambiente ou do arquivo ~/.hermes/.env."""
    key = os.environ.get("JULES_API_KEY")
    if key:
        return key.strip()

    # Fallback para ~/.hermes/.env
    from hermes_constants import get_hermes_home
    env_file = get_hermes_home() / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("JULES_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    raise ValueError("JULES_API_KEY not found in environment or ~/.hermes/.env")


def start_jules_task(
    prompt: str,
    branch: str,
    api_key: str,
    repo_slug: str,
) -> str:
    """Cria uma nova sessão no Google Jules e retorna o session name."""
    import urllib.request

    url = f"{JULES_API_BASE}/sessions"
    headers = {
        "X-Goog-Api-Key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "source": f"sources/github/{repo_slug}",
        "reference": f"refs/heads/{branch}",
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["name"]


def get_session_status(session_name: str, api_key: str) -> Dict[str, Any]:
    """Consulta o estado de uma sessão do Jules."""
    import urllib.request

    clean_name = session_name.removeprefix("sessions/")
    url = f"{JULES_API_BASE}/sessions/{clean_name}"
    headers = {"X-Goog-Api-Key": api_key}
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_completion(
    session_name: str,
    api_key: str,
    timeout_min: int = 30,
    poll_interval_sec: int = 15,
) -> Dict[str, Any]:
    """Aguarda até que a tarefa atinja um estado terminal."""
    start = time.time()
    while (time.time() - start) < (timeout_min * 60):
        data = get_session_status(session_name, api_key)
        state = data.get("state", "UNKNOWN")
        print(f"[Jules] State: {state}")
        if state in ("SUCCEEDED", "COMPLETED"):
            return data
        elif state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Jules task ended with state: {state}")
        time.sleep(poll_interval_sec)
    raise TimeoutError(f"Jules task '{session_name}' timed out after {timeout_min} minutes.")


def cmd_dispatch(args: argparse.Namespace) -> None:
    """Dispara uma tarefa para o Google Jules."""
    api_key = get_api_key()
    repo_slug = get_github_repo_slug()

    # 1. Definir nome da branch de trabalho
    base_branch = get_current_branch()
    task_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", args.prompt[:30].strip()).strip("-").lower()
    work_branch = args.branch or f"jules/{int(time.time())}-{task_slug}"

    print(f"[*] Repositório: {repo_slug}")
    print(f"[*] Criando branch de trabalho efêmera: {work_branch}")

    # Cria e faz push da branch se solicitada
    if not args.existing_branch:
        run_cmd(f"git checkout -b {work_branch}")
        run_cmd(f"git push -u origin {work_branch}")
    else:
        work_branch = base_branch

    # 2. Chama a API do Jules
    print(f"[*] Despachando sessão para Google Jules...")
    session_name = start_jules_task(
        prompt=args.prompt,
        branch=work_branch,
        api_key=api_key,
        repo_slug=repo_slug,
    )
    print(f"[+] Sessão criada com sucesso: {session_name}")

    if not args.wait:
        print(json.dumps({
            "status": "DISPATCHED",
            "session_name": session_name,
            "branch": work_branch,
            "repo_slug": repo_slug,
            "hint": f"Verifique o status com: python3 skills/autonomous-ai-agents/google-jules/scripts/jules_worker.py status --session {session_name}",
        }, indent=2))
        return

    # 3. Aguarda término
    print(f"[*] Aguardando conclusão da sessão (timeout: {args.timeout}m)...")
    res = wait_for_completion(session_name, api_key, timeout_min=args.timeout)
    print(f"[+] Tarefa concluída com sucesso: {res.get('state')}")

    if args.sync_back:
        print(f"[*] Sincronizando alterações da branch remota 'origin/{work_branch}'...")
        run_cmd(f"git fetch origin {work_branch}")
        run_cmd(f"git checkout {base_branch}")
        run_cmd(f"git merge origin/{work_branch} --no-edit")
        print(f"[+] Merge concluído na branch base '{base_branch}'.")


def cmd_status(args: argparse.Namespace) -> None:
    """Inspeciona o status atual de uma sessão."""
    api_key = get_api_key()
    data = get_session_status(args.session, api_key)
    print(json.dumps(data, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Google Jules CLI Worker for Hermes Agent.")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # dispatch
    p_disp = sub.add_parser("dispatch", help="Dispatch an async coding task to Google Jules.")
    p_disp.add_argument("--prompt", required=True, help="Task prompt describing the changes.")
    p_disp.add_argument("--branch", help="Target branch (generated automatically if omitted).")
    p_disp.add_argument("--existing-branch", action="store_true", help="Use current branch as-is.")
    p_disp.add_argument("--wait", action="store_true", help="Wait for task completion synchronously.")
    p_disp.add_argument("--sync-back", action="store_true", help="Auto-merge remote branch back on success.")
    p_disp.add_argument("--timeout", type=int, default=30, help="Timeout in minutes.")
    p_disp.set_defaults(func=cmd_dispatch)

    # status
    p_stat = sub.add_parser("status", help="Check status of an ongoing Jules session.")
    p_stat.add_argument("--session", required=True, help="Session name or ID (e.g. sessions/12345).")
    p_stat.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
