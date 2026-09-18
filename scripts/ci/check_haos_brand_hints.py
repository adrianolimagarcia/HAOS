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
- Varre tambem COMENTARIOS (token COMMENT): texto de dev citando o binario do
  upstream envelhece igual. Aqui o marcador entra na propria linha do comentario
  (ou na anterior), ex.: ``# haos auth ... # haos-brand: historico``.
- ``tests/`` e ``website/`` ficam fora: fixture e o site de documentacao publica
  (nao embarcado no appliance). ``skills/``, ``optional-skills/`` e ``docs/`` ENTRAM:
  o agente EXECUTA o comando que a skill manda rodar, entao hint velho ali quebra
  a tarefa, nao e cosmetica.
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
import io
import re
import sys
import tokenize
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
# ``vault`` entrou junto com o registro de ``build_vault_parser`` em hermes_cli/main.py:
# ate entao o comando nao existia no ``--help`` e as strings de hermes_cli/vault.py
# mandavam o operador rodar ``hermes vault add`` (binario ausente no appliance).
COMMANDS = (
    "acp|approvals|auth|backup|browser|bundles|chat|checkpoints|claw|codebase-wiki|completion|"
    "computer-use|config|console|cron|curator|dashboard|debug|desktop|doctor|dump|egress|fallback|"
    "gateway|gui|haos|hooks|import|import-agent|insights|journey|kanban|learning|login|logout|logs|lsp|"
    "mcp|memory-graph|memory|migrate|moa|model|monitoring|pairing|pause|peer|pets|plugins|portal|"
    "profile|project|prompt-size|proxy|resume|secrets|security|send|serve|sessions|setup|skills|"
    "skin|slack|status|sync|tools|uninstall|update|vault|verify|webhook|whatsapp|whatsapp-cloud|"
    "worktree|models|plugin|skins"
)
# Esta lista deve cobrir TODO subcomando que o parser constroi; um nome faltando e um falso
# NEGATIVO silencioso, que e o modo de falha perigoso aqui (o guard nao ve o site e ninguem
# descobre). Dois ja custaram caro: `logout` estava e `login` nao, entao a mensagem de
# deprecacao em hermes_cli/auth.py escapou; e `desktop`/`gui` faltavam, entao os prints de
# update_cmd_*.py e main_desktop.py escaparam. Confira com:
#   python -c "import argparse,sys; sys.path.insert(0,'scripts/ci'); import check_haos_brand_hints as g; \
#     from hermes_cli.main import _build_cli_parser; p,_=_build_cli_parser(); \
#     s=[a for a in p._actions if isinstance(a,argparse._SubParsersAction)][0]; \
#     print(sorted(set(s.choices)-set(g.COMMANDS.split('|'))))"
# `models`/`plugin`/`skins` nao sao subcomandos: sao as frases de marca que a varredura
# converteu, mantidas de proposito.
# `memory-graph` vem antes de `memory` para o alternador casar o nome longo primeiro.

# Conteudo markdown que o agente executa (skill) + docs do fork.
CONTENT_DIRS = ("skills", "optional-skills", "docs")
CONTENT_SUFFIXES = (".md", ".mdx")

# `hermes <subcomando>` casa sozinho o caso classico. Isto cobre o ponto cego que deixou o
# proprio `--help` meio convertido: `hermes --tui`, `hermes -w`, `hermes -p <nome>` nomeiam
# o binario upstream SEM nomear subcomando, entao a primeira alternativa nunca os via —
# `haos --help` mandava o operador rodar `hermes --tui`, binario que nao existe no appliance.
# Espaco literal, nao `\s`: `scan()` filtra barato por `"hermes "` no texto antes de parsear
# a AST, e um `\s` casaria tab/quebra de linha que esse pre-filtro nao enxerga. Medido: os
# dois dao o mesmo numero de ocorrencias, entao usar espaco nao perde nada.
FLAG_HINT = r"(?<![\w./~-])hermes --?[A-Za-z]"
PATTERN = re.compile(r"\bhermes (?:%s)\b(?!\.)|%s" % (COMMANDS, FLAG_HINT))
# Markdown fica no padrao de subcomando, sem a segunda alternativa. Em .py a marca e
# renderizavel em runtime (`product_command()`), entao nome upstream fixo e defeito; em
# prosa nao e, e o texto frequentemente registra argv real, medicao real e o usuario
# `hermes` da imagem Docker (um doc de auditoria mede `hermes --help` ~ 0,4 s). Aplicar a
# regra de flag ali daria falso positivo em documento correto. Nada foi relaxado: a regra
# de subcomando continua valendo em markdown.
#
# `haos` sai do padrao de markdown, e SO dele. O subcomando existe — o parser renderiza
# `usage: haos haos [-h] {status,doctor,...}` — mas em prosa `hermes haos` e ambiguo de um
# jeito que em .py nao e, e as tres razoes foram medidas:
#   * o README do proprio fork manda digitar `haos status`, nunca `haos haos status`;
#   * `HAOS_HOME=~/.hermes haos doctor` e o PATH do home seguido do binario, e
#     `exec s6-setuidgid hermes haos -p coder` e o USUARIO de servico seguido do binario:
#     os dois casam `hermes haos` sem serem o subcomando (2 falsos positivos medidos);
#   * docs/pr/ registram o que foi submetido ao upstream, onde `hermes haos` esta correto —
#     reescrever falsificaria o registro.
# Reescrever os 16 sites dos ADRs para `haos haos` contradiria a doc do fork. Nada foi
# relaxado: markdown mantem 100% da cobertura que ja tinha (as 71 entradas anteriores),
# apenas nao ganha esta, cuja semantica em prosa e ambigua. Em .py os 48 sites foram
# convertidos, entao a cobertura nova vale integralmente onde ela e inequivoca.
CONTENT_COMMANDS = COMMANDS.replace("|haos|", "|")
CONTENT_PATTERN = re.compile(r"\bhermes (?:%s)\b(?!\.)" % CONTENT_COMMANDS)
MARKER = "haos-brand:"
MARKER_RE = re.compile(re.escape(MARKER) + r"\s*\S")


def _iter_files():
    # Modulos root-level (cli.py, run_agent.py, hermes_state*.py, toolsets.py, ...) sao
    # runtime do fork e ficaram fora do SCAN_DIRS ate 19/09/2026: 47 hints passaram
    # despercebidos e `haos doctor` imprimia "Run 'hermes setup'". Varre-los aqui.
    for path in sorted(REPO_ROOT.glob("*.py")):
        yield path
    for d in SCAN_DIRS:
        root = REPO_ROOT / d
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if set(path.parts) & {"__pycache__", ".venv", "venv", "node_modules", "target"}:
                continue
            yield path


def _iter_content_files():
    for d in CONTENT_DIRS:
        root = REPO_ROOT / d
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in CONTENT_SUFFIXES:
                continue
            if set(path.parts) & {"__pycache__", "node_modules", "target"}:
                continue
            yield path


def _scan_content(offenders: list[str], allowed: list[str]) -> None:
    """Markdown: linha a linha (comentario/lista nao tem AST). O marcador vale na
    propria linha ou na anterior, igual ao caso do codigo."""
    for path in _iter_content_files():
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel = path.relative_to(REPO_ROOT)
        for i, line in enumerate(lines):
            match = CONTENT_PATTERN.search(line)
            if not match:
                continue
            context = line + "\n" + (lines[i - 1] if i else "")
            entry = f"{rel}:{i + 1}: {line.strip()[:140]} (hint: {match.group(0)!r})"
            (allowed if MARKER_RE.search(context) else offenders).append(entry)


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

        # Comentarios: nao aparecem na AST, entao vao por tokenize.
        try:
            toks = tokenize.generate_tokens(io.StringIO(source).readline)
        except (tokenize.TokenError, IndentationError, OSError):
            toks = []
        for t in toks:
            if t.type != tokenize.COMMENT:
                continue
            match = PATTERN.search(t.string)
            if not match:
                continue
            lineno = t.start[0]
            text = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
            context = (lines[lineno - 1] if 0 < lineno <= len(lines) else "") + "\n" + (
                lines[lineno - 2] if lineno >= 2 else ""
            )
            entry = f"{rel}:{lineno}: {text} (hint: {match.group(0)!r})"
            (allowed if MARKER_RE.search(context) else offenders).append(entry)

    _scan_content(offenders, allowed)
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
        print("Em markdown de skill/doc escreva 'haos auth' literal.")
        print(f"Se a mencao for intencional, adicione '{MARKER} <motivo>' na linha ou na anterior.\n")
        for item in offenders:
            print(f"  {item}")
        return 1

    print(f"OK: nenhum hint user-facing citando 'hermes <cmd>' ({len(allowed)} site(s) marcado(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
