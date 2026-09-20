#!/usr/bin/env python3
"""A/B de alocador de memória (glibc malloc vs jemalloc) para os serviços HAOS.

Mede pico (``VmHWM``) e regime (``VmRSS``) de processos-filho reais, para decidir
com número — não com folclore — se vale preloadar jemalloc nos serviços Python.

Contexto (2026-09-19): o gateway do HAOS mostrava ``Private_Dirty`` de ~321 MB
com ``Shared_Clean`` de apenas ~13 MB, isto é, o custo residente é heap anônimo
de malloc, não bibliotecas. A assinatura no ``smaps`` (dezenas de regiões
``rw-p`` anônimas de 8-28 MB) é arena de glibc por thread. Este harness mede se
o jemalloc devolve essa memória ao SO.

Uso::

    python scripts/bench_haos_memory.py                    # todos os workloads
    python scripts/bench_haos_memory.py --workload arena   # um só
    python scripts/bench_haos_memory.py --repeat 5 --json  # saída de máquina

O harness NÃO toca serviços em execução: cada medição é um processo-filho
descartável que reporta a própria memória lida de ``/proc/self/status``.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys

DEFAULT_PRELOAD = "/usr/lib/libjemalloc.so.2"
_STATUS_FIELDS = ("VmHWM", "VmRSS")


def _read_status(field: str) -> int:
    """Lê um campo kB de /proc/self/status (0 quando ausente)."""
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(field):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


# --------------------------------------------------------------------------
# Workloads — cada um roda DENTRO do processo-filho.
# --------------------------------------------------------------------------
def _wl_arena(threads: int = 16, iters: int = 120, chunk_kb: int = 64) -> object:
    """Churn alocado em muitas threads: reproduz a arena por thread do glibc.

    Cada thread aloca e libera blocos de ``chunk_kb`` repetidamente e retém uma
    cauda curta. O glibc tende a manter as páginas liberadas nas arenas das
    threads; o jemalloc as decai de volta ao SO.
    """
    import threading

    chunk = chunk_kb * 1024
    retained: list[list[bytearray]] = []
    lock = threading.Lock()

    def worker() -> None:
        local: list[bytearray] = []
        for _ in range(iters):
            buf = bytearray(chunk)
            buf[0] = 1  # força o page-fault da página
            local.append(buf)
            if len(local) > 4:
                local.pop(0)
        with lock:
            retained.append(local)

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    return retained


def _wl_webui_import() -> object:
    """Custo de import do control plane Python do HAOS."""
    import hermes.platform.webui.standalone as standalone

    return standalone.__name__


def _wl_webui_server() -> object:
    """Import + construção real do servidor standalone (o que o serviço faz)."""
    import tempfile

    import hermes.platform.webui.standalone as standalone

    data_dir = tempfile.mkdtemp(prefix="haos-bench-")
    server, _state, _url = standalone.make_standalone_server(
        data_dir=data_dir, host="127.0.0.1", port=0
    )
    server.server_close()
    return data_dir


WORKLOADS = {
    "arena": _wl_arena,
    "webui-import": _wl_webui_import,
    "webui-server": _wl_webui_server,
}


def _run_child(workload: str, preload: str | None, settle_s: float) -> dict[str, int]:
    """Roda um workload em processo-filho e devolve {peak_kb, steady_kb}."""
    env = dict(os.environ)
    env.pop("LD_PRELOAD", None)
    if preload:
        env["LD_PRELOAD"] = preload
    env.setdefault("PYTHONPATH", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--child", workload, "--settle", str(settle_s)],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"workload {workload!r} falhou (exit {proc.returncode}):\n{proc.stderr.strip()[-2000:]}"
        )
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT ") :])
    raise RuntimeError(f"workload {workload!r} não emitiu RESULT:\n{proc.stdout.strip()[-2000:]}")


def _child_main(workload: str, settle_s: float) -> int:
    fn = WORKLOADS[workload]
    keep = fn()  # noqa: F841 — manter vivo até a medição
    import gc
    import time

    gc.collect()
    time.sleep(settle_s)  # deixa o alocador decair/consolidar antes do regime
    peak = _read_status("VmHWM")
    steady = _read_status("VmRSS")
    print(f"RESULT {json.dumps({'peak_kb': peak, 'steady_kb': steady})}", flush=True)
    return 0


def _median(values: list[int]) -> int:
    return int(statistics.median(values))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workload", choices=sorted(WORKLOADS), help="workload único (default: todos)")
    parser.add_argument("--repeat", type=int, default=3, help="repetições por configuração (default: 3)")
    parser.add_argument("--preload", default=DEFAULT_PRELOAD, help="caminho do libjemalloc.so")
    parser.add_argument("--settle", type=float, default=2.0, help="segundos de espera antes do regime")
    parser.add_argument("--json", action="store_true", help="emitir JSON em vez de tabela")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.child:
        return _child_main(args.child, args.settle)

    if not os.path.exists(args.preload):
        print(f"erro: preload não encontrado: {args.preload}", file=sys.stderr)
        return 2

    names = [args.workload] if args.workload else sorted(WORKLOADS)
    report: dict[str, object] = {"preload": args.preload, "repeat": args.repeat, "workloads": {}}

    for name in names:
        base = [_run_child(name, None, args.settle) for _ in range(args.repeat)]
        jem = [_run_child(name, args.preload, args.settle) for _ in range(args.repeat)]
        base_peak, jem_peak = _median([r["peak_kb"] for r in base]), _median([r["peak_kb"] for r in jem])
        base_steady, jem_steady = _median([r["steady_kb"] for r in base]), _median([r["steady_kb"] for r in jem])

        def _delta(before: int, after: int) -> float:
            return (after - before) / before * 100.0 if before else 0.0

        report["workloads"][name] = {
            "baseline_peak_kb": base_peak,
            "jemalloc_peak_kb": jem_peak,
            "peak_delta_pct": round(_delta(base_peak, jem_peak), 1),
            "baseline_steady_kb": base_steady,
            "jemalloc_steady_kb": jem_steady,
            "steady_delta_pct": round(_delta(base_steady, jem_steady), 1),
        }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"preload: {args.preload}   repetições: {args.repeat}   (mediana)\n")
    header = f"{'workload':<16} {'pico base':>11} {'pico jem':>11} {'Δ pico':>9} {'regime base':>13} {'regime jem':>13} {'Δ regime':>10}"
    print(header)
    print("-" * len(header))
    for name, row in report["workloads"].items():  # type: ignore[union-attr]
        print(
            f"{name:<16} "
            f"{row['baseline_peak_kb'] / 1024:>9.1f}M "
            f"{row['jemalloc_peak_kb'] / 1024:>9.1f}M "
            f"{row['peak_delta_pct']:>8.1f}% "
            f"{row['baseline_steady_kb'] / 1024:>11.1f}M "
            f"{row['jemalloc_steady_kb'] / 1024:>11.1f}M "
            f"{row['steady_delta_pct']:>9.1f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
