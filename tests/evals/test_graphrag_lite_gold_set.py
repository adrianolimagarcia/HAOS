"""CI guard of the graphrag-lite gold set (backlog HAOS P1 — Etapa 2).

O gold set vive em ``evals/graphrag_lite/cases/*.json`` e roda pelo runner
in-repo com importacoes reais (E2E contra a maquinaria de producao). Estes
testes garantem que ele existe, esta bem-formado e esta 100% verde em CI —
sem leitura de codigo-fonte e sem tocar ~/.hermes (o runner isola HERMES_HOME
por subprocesso).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO / "evals" / "graphrag_lite"
CASES_DIR = EVAL_DIR / "cases"

from evals.graphrag_lite import impl, runner  # noqa: E402


def _cases() -> list[dict]:
    out = []
    for f in sorted(CASES_DIR.glob("*.json")):
        out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def test_gold_set_is_well_formed() -> None:
    """Cada caso tem id unico (== arquivo), pergunta, descricao e grader python."""
    ids = set()
    for case in _cases():
        cid = case["id"]
        assert (CASES_DIR / f"{cid}.json").exists(), f"id {cid} != nome do arquivo"
        assert cid not in ids, f"id duplicado: {cid}"
        ids.add(cid)
        for field in ("pergunta", "descricao", "tags", "timeout"):
            assert field in case, f"{cid}: faltando campo {field}"
        g = case["grader"]
        assert g["type"] == "python", f"{cid}: grader != python"
        assert g["code"], f"{cid}: grader sem code"
    assert len(ids) >= 20, f"gold set com {len(ids)} casos (< 20)"


def test_every_case_has_an_implementation() -> None:
    """Nenhum caso decorativo: todo id tem implementacao executavel."""
    for case in _cases():
        assert case["id"] in impl._CASES, f"{case['id']}: sem implementacao"
    # e toda implementacao tem caso (sem mortos).
    for cid in impl._CASES:
        assert any(c["id"] == cid for c in _cases()), f"{cid}: implementacao sem caso"


def test_full_gold_set_is_green() -> None:
    """Gate: os 21 casos passam contra a maquinaria real (exit 0 = zero falhas)."""
    assert runner.main([]) == 0


def test_runner_isolates_hermes_home_per_case() -> None:
    """Nenhum caso pode escrever em ~/.hermes: o runner aponta HERMES_HOME
    para um tempdir fresco por subprocesso (contrato da regra dura)."""
    captured: dict = {}

    def _spy(*a, **kw):
        captured["env"] = kw.get("env") or (a[2] if len(a) > 2 else {})
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    import evals.graphrag_lite.runner as r

    orig = r.subprocess.run
    r.subprocess.run = _spy
    try:
        r.run_grader({"id": "spy", "grader": {"type": "python", "code": "pass"}})
    finally:
        r.subprocess.run = orig

    home = captured["env"].get("HERMES_HOME", "")
    assert home, "HERMES_HOME nao definido pelo runner"
    assert home.startswith(tempfile.gettempdir()), f"HERMES_HOME fora de tempdir: {home}"
    assert ".hermes" not in home
    assert str(REPO) in captured["env"].get("PYTHONPATH", ""), "PYTHONPATH sem o repo"


def test_cases_have_no_machine_local_paths() -> None:
    """Casos nao carregam paths da instalacao viva (nao portaveis)."""
    bad = re.compile(r"/run/media/|/root/\.hermes|/home/[a-z0-9_-]+/")
    for case in _cases():
        blob = json.dumps(case, ensure_ascii=False)
        m = bad.search(blob)
        assert not m, f"{case['id']}: path maquina-local {m.group(0)!r}"
