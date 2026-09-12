#!/usr/bin/env python3
"""Runner do gold set graphrag-lite (backlog HAOS P1).

Mesmo CONTRATO do runner de haos-evals (exit code = numero de falhas; 0 = gate
verde), mas com raiz no repo e seguro para CI:

- os casos vivem em ``cases/*.json`` ao lado deste arquivo;
- cada grader ``python`` roda num subprocesso fresco com ``PYTHONPATH`` apontando
  para a raiz do repo, ``cwd`` na raiz e ``HERMES_HOME`` num tempdir NOVO por
  caso — nenhum caso escreve em ~/.hermes nem depende de instalacao externa;
- TZ/LANG fixos (paridade com scripts/run_tests.sh).

Formato do caso (compativel com o runner de haos-evals):

    {"id", "pergunta", "descricao", "tags", "timeout",
     "grader": {"type": "python", "code": "from evals.graphrag_lite.impl import run_case; run_case('<id>')"}}

Uso:

    python evals/graphrag_lite/runner.py             # roda todos (gate)
    python evals/graphrag_lite/runner.py --list      # lista
    python evals/graphrag_lite/runner.py --tag rag   # filtro por tag
    python evals/graphrag_lite/runner.py --json      # saida para maquina
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = Path(__file__).resolve().parent / "cases"

_PRELUDE = (
    "import sys; sys.path.insert(0, r'{root}')"
).format(root=str(REPO_ROOT))


def load_cases() -> list[dict]:
    cases = []
    for f in sorted(CASES_DIR.glob("*.json")):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
            c["_file"] = f.name
            cases.append(c)
        except Exception as exc:  # noqa: BLE001
            cases.append({"id": f.stem, "_file": f.name,
                          "descricao": f"JSON invalido: {exc}",
                          "tags": ["broken"],
                          "grader": {"type": "shell", "cmd": "false"}})
    return cases


def run_grader(case: dict) -> tuple[bool, str]:
    g = case.get("grader") or {}
    gtype = g.get("type", "shell")
    timeout = int(case.get("timeout", g.get("timeout", 120)))

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    env["TZ"] = "UTC"
    env["LANG"] = "C.UTF-8"
    # Isolamento por caso: HERMES_HOME fresco -> nenhum caso toca ~/.hermes.
    with tempfile.TemporaryDirectory(prefix="graphrag-lite-eval-") as home:
        env["HERMES_HOME"] = home

        if gtype == "shell":
            cmd = g.get("cmd")
            if not cmd:
                return False, "grader shell sem 'cmd'"
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                   timeout=timeout, env=env, cwd=str(REPO_ROOT))
            except subprocess.TimeoutExpired:
                return False, f"timeout apos {timeout}s"
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            expect = int(g.get("expect_exit", 0))
            if r.returncode != expect:
                return False, f"exit={r.returncode} (esperado {expect}) :: {err[:200] or out[:200]}"
            return True, (out[:150] or "ok")

        if gtype == "python":
            code = g.get("code")
            if not code:
                return False, "grader python sem 'code'"
            full = f"{_PRELUDE}\n{code}"
            try:
                r = subprocess.run([sys.executable, "-c", full],
                                   capture_output=True, text=True,
                                   timeout=timeout, env=env, cwd=str(REPO_ROOT))
            except subprocess.TimeoutExpired:
                return False, f"timeout apos {timeout}s"
            if r.returncode != 0:
                return False, f"exit={r.returncode} :: {(r.stderr or r.stdout).strip()[:200]}"
            return True, (r.stdout or "").strip()[:150] or "ok"

    return False, f"grader type desconhecido: {gtype}"


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", action="append", default=[],
                    help="roda so casos com esta tag (repetivel)")
    ap.add_argument("--list", action="store_true", help="lista os casos e sai")
    ap.add_argument("--json", action="store_true", help="saida em JSON")
    args = ap.parse_args(argv)

    cases = load_cases()
    if args.tag:
        cases = [c for c in cases if set(args.tag) & set(c.get("tags") or [])]

    if args.list:
        for c in cases:
            print(f"{c.get('id','?'):36s} [{'/'.join(c.get('tags') or [])}] "
                  f"{c.get('pergunta','')[:60]}")
        print(f"\n{len(cases)} caso(s) em {CASES_DIR}")
        return 0

    results, failed, skipped = [], 0, 0
    for c in cases:
        cid = c.get("id", c["_file"])
        miss = c.get("skip_if_missing")
        if miss and not Path(miss).exists():
            skipped += 1
            results.append({"id": cid, "status": "SKIP", "detail": f"ausente: {miss}"})
            continue
        ok, detail = run_grader(c)
        results.append({"id": cid, "status": "PASS" if ok else "FAIL",
                        "detail": detail, "tags": c.get("tags") or []})
        if not ok:
            failed += 1

    if args.json:
        print(json.dumps({"total": len(results), "failed": failed, "skipped": skipped,
                          "results": results}, indent=2, ensure_ascii=False))
    else:
        print(f"=== gold set graphrag-lite — {len(results)} caso(s) ===")
        for r in results:
            print(f"[{r['status']}] {r['id']:36s} {r['detail'][:120]}")
        print(f"--- {len(results)-failed-skipped} ok | {failed} falha(s) | "
              f"{skipped} pulado(s) ---")
    return failed


if __name__ == "__main__":
    sys.exit(main())
