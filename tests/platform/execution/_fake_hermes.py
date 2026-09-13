#!/usr/bin/env python3
"""Fake `hermes` peer para o contrato da lane agêntica (tests only).

Servidor scriptado stdlib (precedente: capabilities/_mock_lsp_server.py):
emula o contrato HermesCliLaneWorker -> worker canônico sem exigir runtime
Hermes/chaves de modelo.

Comportamento dirigido por env:
- ``HAOS_FAKE_LOG``   (obrigatório) — arquivo de log; cada execução anexa uma
  linha JSON com {argv, cwd, env:{k:v...}} (env mostra só chaves selecionadas).
- ``HAOS_FAKE_MODE``  ok (default) | exit_nonzero | bad_json | help_nonzero.
  * ok:      escreve .haos/result.json válido e sai 0.
  * exit_nonzero: não escreve result.json e sai rc 3.
  * bad_json: escreve .haos/result.json com conteúdo inválido e sai 0.
- ``--help`` sempre imprime usage e sai 0 (probe do available()).

Executado como subprocesso real via sys.executable (ou direto) pelo teste.
"""

import json
import os
import sys
import time

_KEYS = ("TERMINAL_CWD", "HAOS_TASK_ID", "HERMES_HOME",
         "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK",
         "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_WORKSPACE",
         "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_TUI")


def _log(argv, cwd, env):
    log_path = env.get("HAOS_FAKE_LOG")
    if not log_path:
        return
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "argv": argv,
            "cwd": cwd,
            "env": {k: env.get(k, "<unset>") for k in _KEYS},
        }) + "\n")


def main() -> int:
    argv = sys.argv[1:]
    env = os.environ
    cwd = os.getcwd()
    _log(argv, cwd, env)

    if argv and argv[0] == "--help":
        print("usage: hermes [--cli] [--accept-hooks] chat -q <query>")
        return 0

    mode = env.get("HAOS_FAKE_MODE", "ok")
    if mode == "help_nonzero":
        return 2  # probe --help falha (available() False)

    if mode == "sleep":
        # Emula worker agêntico que demora além do timeout do control-plane:
        # dorme o suficiente p/ o teste abortar (deadline/órfão) e não escreve
        # result.json antes de morrer.
        duration = float(env.get("HAOS_FAKE_SLEEP", "30"))
        time.sleep(duration)
        return 0

    if mode == "exit_nonzero":
        sys.stderr.write("fake hermes: boom\n")
        return 3

    if mode == "slow_ok":
        # Emula worker saudável que leva um tempo (p/ o teste de heartbeat
        # observar beats > 0 durante a execução): dorme HAOS_FAKE_SLEEP e
        # depois completa (result.json + rc 0).
        time.sleep(float(env.get("HAOS_FAKE_SLEEP", "0.5")))
        haos_dir = os.path.join(cwd, ".haos")
        os.makedirs(haos_dir, exist_ok=True)
        with open(os.path.join(haos_dir, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "summary": "done slowly by fake",
                "evidence": {"fake": True},
                "artifacts": [],
                "residual_risk": [],
            }, fh)
        return 0

    haos_dir = os.path.join(cwd, ".haos")
    os.makedirs(haos_dir, exist_ok=True)
    result_path = os.path.join(haos_dir, "result.json")
    if mode == "bad_json":
        with open(result_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        return 0
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump({
            "summary": "done by fake",
            "evidence": {"fake": True},
            "artifacts": ["a.txt"],
            "residual_risk": [],
        }, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
