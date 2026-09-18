"""Contrato de branding: o texto que o usuario LE nao pode citar o CLI upstream.

O guard estatico (``scripts/ci/check_haos_brand_hints.py``) casa
``hermes <subcomando>`` — preciso, mas cego para ``hermes`` nu, ``hermes --flag`` e
``hermes -p X``. Foi por esse buraco que o ``--help`` do proprio CLI ficou meio
convertido: ``haos --help`` mandava rodar ``hermes --tui``, um binario que nao existe
no appliance.

Este teste fecha a classe pelo outro lado: em vez de casar token no FONTE, ele
RENDERIZA o output real sob ``HAOS_HOME`` e afirma que nenhuma referencia de comando
sobrou. Um guard estatico nunca vera a string que o argparse monta em runtime; o help
renderizado, sim. Cobre 528 parsers (topo + todos os subcomandos, recursivamente)
derivados do proprio parser — nao de uma lista mantida a mao, que envelheceria.

Por que subprocesso: ``_EPILOGUE`` (``hermes_cli/_parser.py``) e uma constante de
modulo avaliada no IMPORT, entao ``HAOS_HOME`` precisa estar setado antes de
``hermes_cli`` ser importado. O autouse de ``tests/conftest.py`` limpa ``HAOS_HOME``
por teste, entao nao da para setar isso in-process sem ``importlib.reload`` (que
quebraria a identidade dos modulos para os outros testes). Um subprocesso com o env
certo e exatamente como o CLI roda de verdade.

Anti-vacuidade: o mesmo probe roda tambem em modo upstream e TEM que achar
referencias. Um teste que passa verde por nao olhar nada e pior que nenhum teste.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hermes_constants import product_cli_name, product_command

REPO_ROOT = Path(__file__).resolve().parent.parent

# Referencia de COMANDO ao CLI: `hermes -w`, `hermes --resume <id>`, `hermes -p <n> ...`.
# Exige uma FLAG depois do nome, o que separa uma invocacao de (a) prosa que usa o nome do
# produto em minuscula ("hermes uses", "hermes will switch"), (b) um path (`<hermes home>`)
# e (c) um valor de exemplo entre aspas (`'hermes'` como substring de titulo de sessao).
# Esses tres aparecem no help renderizado e NAO sao referencias de comando.
UPSTREAM_COMMAND = r"(?<![\w./~-])hermes\s+--?[A-Za-z]"

_PROBE = r'''
import argparse
import re
import sys

pattern = sys.argv[1]

from hermes_cli.main import _build_cli_parser

parser, _subparsers = _build_cli_parser()


def walk(p, path=""):
    yield path or "<top>", p
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from walk(sub, (path + " " + name).strip())


rendered = 0
hits = []
for path, p in walk(parser):
    try:
        text = p.format_help()
    except Exception:
        continue
    rendered += 1
    if re.search(pattern, text):
        line = next(l.strip() for l in text.splitlines() if re.search(pattern, l))
        hits.append(path + ": " + line[:90])

print("RENDERED", rendered)
print("HITS", len(hits))
for h in hits:
    print("  ", h)
'''


def _run_probe(env_overrides):
    env = {k: v for k, v in os.environ.items() if k != "HAOS_HOME"}
    for key, value in env_overrides.items():
        env[key] = value
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, UPSTREAM_COMMAND],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _field(stdout, name):
    for line in stdout.splitlines():
        if line.startswith(name + " "):
            return int(line.split()[1])
    raise AssertionError("probe did not report " + name + ":\n" + stdout)


def test_product_command_renders_the_active_brand(monkeypatch):
    """O primitivo dos dois lados: a mesma chamada rende o nome do produto ativo."""
    monkeypatch.setenv("HAOS_HOME", "/tmp/haos-brand-contract")
    assert product_cli_name() == "haos"
    assert product_command("doctor") == "haos doctor"
    assert product_command("-w") == "haos -w"

    monkeypatch.delenv("HAOS_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", "/tmp/hermes-brand-contract")
    assert product_cli_name() == "hermes"
    assert product_command("doctor") == "hermes doctor"
    assert product_command("-w") == "hermes -w"


def test_cli_help_under_haos_names_no_upstream_command():
    """Todo o help do CLI sob HAOS: zero referencia ao comando upstream."""
    code, out = _run_probe({"HAOS_HOME": "/tmp/haos-brand-contract"})
    assert code == 0, "probe failed (exit %s):\n%s" % (code, out)
    assert "Traceback" not in out, out
    rendered = _field(out, "RENDERED")
    assert rendered > 400, (
        "probe rendered only %s parsers; it stopped seeing the tree:\n%s" % (rendered, out)
    )
    assert _field(out, "HITS") == 0, (
        "help renderizado sob HAOS cita o comando upstream — no appliance esse binario "
        "nao existe:\n" + out
    )


def test_the_probe_actually_detects_upstream_commands():
    """Anti-vacuidade: em modo upstream o mesmo probe TEM que achar referencias.

    Sem isto, uma mudanca que quebrasse o probe (ou o `_build_cli_parser`) o deixaria
    verde por nao olhar nada.
    """
    code, out = _run_probe({"HERMES_HOME": "/tmp/hermes-brand-contract"})
    assert code == 0, "probe failed (exit %s):\n%s" % (code, out)
    assert _field(out, "HITS") > 0, (
        "o probe nao achou NENHUMA referencia nem em modo upstream — ele parou de "
        "inspecionar o help, entao o teste HAOS acima e vacuo:\n" + out
    )
