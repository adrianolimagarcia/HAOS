#!/usr/bin/env python3
"""Guard: nenhum default de path pode construir ``~/.hermes`` neste fork.

O home canônico do HAOS é ``~/.haos`` (ver ``hermes_constants``), então um default
novo apontando para ``~/.hermes`` cria store órfão — a classe de split-brain que
produzia bancos invisíveis em instalações fora da ISO. O AGENTS.md do repo já
manda usar ``get_hermes_home()``/``display_hermes_home()`` em vez de literal.

Analisa a AST, não o texto: só acusa CONSTRUÇÃO de path ancorada no HOME do
operador (``Path.home() / ".hermes"``, ``expanduser("~/.hermes")``,
``os.path.join(x, ".hermes")``). Um literal dentro de mensagem/descrição de tool é
dívida de string user-facing, não default de path, e não é acusado aqui.

Existem sites onde a construção é INTENCIONAL (cadeia de candidatos que precisa
olhar o store legado, detecção de migração, guarda do store real do operador).
Esses levam ``# haos-legacy-path: <motivo>`` na própria linha ou na anterior;
o marcador é a única forma de silenciar este guard.

Não é um teste pytest de propósito: teste que lê o texto de um .py testa a forma do
código-fonte, não comportamento (proibido pelo AGENTS.md).

Uso:
    python scripts/ci/check_legacy_hermes_home.py [--list]
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Código de runtime do fork. tests/website/optional-skills ficam de fora: fixtures e
# documentação podem citar o caminho legado à vontade.
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

LEGACY = ".hermes"
MARKER = "haos-legacy-path:"
MARKER_RE = __import__("re").compile(__import__("re").escape(MARKER) + r"\s*\S")


def _is_path_home(node: ast.AST) -> bool:
    """``Path.home()`` / ``_Path.home()`` — qualquer ``<Name>.home()``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "home"
        and isinstance(node.func.value, ast.Name)
    )


def _is_legacy_str(node: ast.AST) -> bool:
    """Constante de string que é exatamente o segmento legado (ou ``~/.hermes/...``)."""
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    value = node.value
    return value == LEGACY or value.startswith(f"~/{LEGACY}")


def _is_expanduser(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Attribute) and node.func.attr == "expanduser")
        or (isinstance(node.func, ast.Name) and node.func.id == "expanduser")
    )


def _is_join(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Attribute) and node.func.attr == "join")
        or (isinstance(node.func, ast.Name) and node.func.id == "join")
    )


class _Finder(ast.NodeVisitor):
    """Coleta linhas onde um path ``~/.hermes`` é CONSTRUÍDO."""

    def __init__(self) -> None:
        self.hits: set[int] = set()

    def visit_BinOp(self, node: ast.BinOp) -> None:
        # Path.home() / ".hermes"  (e encadeamentos: / ".hermes" / "auth.json")
        if isinstance(node.op, ast.Div) and _is_legacy_str(node.right) and _is_path_home(node.left):
            self.hits.add(node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # os.path.expanduser("~/.hermes[/...]")
        if _is_expanduser(node) and node.args and _is_legacy_str(node.args[0]):
            self.hits.add(node.lineno)
        # os.path.join(x, ".hermes", ...)
        if _is_join(node):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == LEGACY:
                    self.hits.add(node.lineno)
                    break
        self.generic_visit(node)


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
    for path in _iter_files():
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            continue
        finder = _Finder()
        finder.visit(tree)
        if not finder.hits:
            continue
        lines = source.splitlines()
        rel = path.relative_to(REPO_ROOT)
        for lineno in sorted(finder.hits):
            text = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
            context = (lines[lineno - 1] if 0 < lineno <= len(lines) else "") + "\n" + (
                lines[lineno - 2] if lineno >= 2 else ""
            )
            entry = f"{rel}:{lineno}: {text}"
            (allowed if MARKER_RE.search(context) else offenders).append(entry)
    return offenders, allowed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="mostra também os sites legados permitidos")
    args = parser.parse_args()

    offenders, allowed = scan()

    if args.list:
        print(f"Sites legados permitidos (marcados com {MARKER}): {len(allowed)}")
        for item in allowed:
            print(f"  {item}")

    if offenders:
        print("FAIL: default de path construindo ~/.hermes (home canônico do fork é ~/.haos).")
        print("Use get_hermes_home()/get_process_hermes_home()/_get_platform_default_hermes_home().")
        print("Se o literal for intencional (cadeia de candidatos legada, detecção de migração,")
        print(f"guarda do store real), adicione '{MARKER} <motivo>' na linha ou na anterior.\n")
        for item in offenders:
            print(f"  {item}")
        return 1

    print(f"OK: nenhum default novo em ~/.hermes ({len(allowed)} site(s) legado(s) marcado(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
