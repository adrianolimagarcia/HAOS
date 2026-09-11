#!/usr/bin/env python3
r"""Guard: hint de comando user-facing nao pode citar o CLI ``hermes``.

Neste fork o CLI e ``haos`` (``product_cli_name()``). Uma mensagem que diz "rode
``haos auth``" instrui um comando que NAO EXISTE no appliance: medido antes desta
varredura, ``command -v hermes`` retornava ausente na VM e no host, e havia 828
instrucoes quebradas em strings user-facing (a classe que fazia ``haos doctor``
mandar o operador rodar ``haos auth``).

Como corrigir:

    from hermes_constants import product_command
    print("Rode `" + product_command("auth") + "` para autenticar.")

Docstring nao pode ser f-string (perde ``__doc__``), entao ali a marca e textual:
escreva ``haos auth`` literal.

CALIBRACAO (para nao barrar caso legitimo):

- Acusa apenas ``hermes <subcomando>`` de uma lista curada com os subcomandos REAIS
  do CLI (extraidos de ``haos --help``; ver COMMANDS abaixo). Prosa que por acaso
  comeca com a palavra hermes nao casa: "the hermes install dir", "hermes
  config.yaml" (o arquivo, barrado pelo ``(?!\.)``) e "hermes agent" (nome do
  projeto, nao subcomando) passam.
- ``tests/``, ``website/``, ``docs/``, ``optional-skills/`` ficam fora: fixture,
  documentacao e skill de terceiro podem citar o nome upstream a vontade.
- Caso INTENCIONAL (ler um alias legado que guarda "hermes <cmd>", comparar com o
  CLI upstream numa deteccao de migracao) leva ``# haos-brand: <motivo>`` na propria
  linha ou na anterior; o marcador e a unica forma de silenciar este guard.

Nao e teste pytest de proposito: teste que le o texto de um .py testa a forma do
codigo-fonte, nao comportamento (proibido pelo AGENTS.md).

Uso:
    python scripts/ci/check_haos_brand_hints.py [--list]
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Codigo de runtime do fork + scripts de operacao. tests/website/docs ficam de fora.
SCAN_DIRS = (
    "hermes",
    "hermes_cli",
    "tools",
    "agent",
    "gateway",
    "plugins",
    "cron",
    "tui_gateway",
    "acp_adapter",
    "packages",
    "scripts",
)

# Subcomandos reais do CLI (``haos --help``) + as frases de marca que a varredura de
# branding converteu ("hermes models"/"hermes plugin"). Um subcomando novo que falte
# aqui e um falso NEGATIVO (deixa passar), nunca um falso positivo (barra codigo bom).
COMMANDS = (
    "acp|approvals|auth|backup|browser|bundles|chat|checkpoints|claw|codebase-wiki|completion|"
    "computer-use|config|console|cron|curator|dashboard|debug|doctor|dump|egress|fallback|"
    "gateway|hooks|import|import-agent|insights|kanban|logout|logs|lsp|mcp|memory|migrate|moa|"
    "model|monitoring|pairing|pause|peer|pets|plugins|portal|profile|project|prompt-size|proxy|"
    "resume|secrets|security|send|serve|sessions|setup|skills|skin|slack|status|sync|tools|"
    "uninstall|update|verify|webhook|whatsapp|whatsapp-cloud|worktree|models|plugin|skins"
)

PATTERN = re.compile(r"\bhermes (?:%s)\b(?!\.)" % COMMANDS)
MARKER = "haos-brand:"
MARKER_RE = re.compile(re.escape(MARKER) + r"\s*\S")


def _iter_files():
    for d in SCAN_DIRS:
        root = REPO_ROOT / d
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if set(path.parts) & {"__pycache__", ".venv", "venv", "node_modules", "target"}:
                continue
            yield path


def scan() -> tuple[list[str], list[str]]:
    """Return (offenders, allowed) as ``path:line: source`` strings."""
    offenders: list[str] = []
    allowed: list[str] = []
    self_path = Path(__file__).resolve()
    for path in _iter_files():
        if path.resolve() == self_path:
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Pre-filtro barato: sem a palavra no texto nao ha o que a AST achar.
        if "hermes " not in source:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        lines = source.splitlines()
        rel = path.relative_to(REPO_ROOT)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            match = PATTERN.search(node.value)
            if not match:
                continue
            lineno = node.lineno
            text = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
            context = (lines[lineno - 1] if 0 < lineno <= len(lines) else "") + "\n" + (
                lines[lineno - 2] if lineno >= 2 else ""
            )
            entry = f"{rel}:{lineno}: {text} (hint: {match.group(0)!r})"
            (allowed if MARKER_RE.search(context) else offenders).append(entry)
    return offenders, allowed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="mostra tambem os sites marcados")
    args = parser.parse_args()

    offenders, allowed = scan()

    if args.list:
        print(f"Sites marcados com {MARKER}: {len(allowed)}")
        for item in allowed:
            print(f"  {item}")

    if offenders:
        print("FAIL: hint user-facing citando o CLI upstream 'hermes' (o CLI deste fork e 'haos').")
        print("Use: \"texto `\" + product_command(\"auth\") + \"` texto\"  (from hermes_constants import product_command).")
        print("Em docstring (nao pode ser f-string) escreva 'haos auth' literal.")
        print(f"Se a mencao for intencional, adicione '{MARKER} <motivo>' na linha ou na anterior.\n")
        for item in offenders:
            print(f"  {item}")
        return 1

    print(f"OK: nenhum hint user-facing citando 'hermes <cmd>' ({len(allowed)} site(s) marcado(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
