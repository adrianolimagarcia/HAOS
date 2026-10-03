"""Heartbeat/TTL do par (claim, filho) da lane agêntica (COMPLIANCE delta 41).

Fecha a lacuna "heartbeat/TTL do filho" do roadmap: enquanto o worker
canônico roda em subprocesso (HermesCliLaneWorker), o control-plane HAOS —
que DETÉM o claim upstream — precisa renovar o claim para o card não ser
reclaimado por outro lane/tick, e precisa abortar quando o claim se perde ou
o prazo estoura. Regra: liveness é do PAR. ``wait_with_heartbeat`` espera o
processo em passos curtos e, a cada passo:
- invoca o ``heartbeat_fn`` (ex.: ``adapter.heartbeat(task_id)``); se ele
  devolve False (claim perdido/reclaimado por outro) -> mata o grupo de
  processo e levanta `HeartbeatLostError`;
- respeita o ``timeout_seconds`` (deadline do max_runtime) -> mata e levanta
  `HeartbeatDeadlineError`.

Nada disso toca o schema upstream (heartbeat_claim/release_stale_claims já
existem lá); a camada apenas conecta a liveness do filho real ao claim.
"""

import os
import subprocess
import time
from typing import Callable, Optional

from hermes.platform.execution.lane_executor import LaneError


class HeartbeatLostError(LaneError):
    """Claim perdido ou filho morto durante a execução (par quebrado)."""


class HeartbeatDeadlineError(LaneError):
    """Prazo máximo de execução estourado; o filho foi morto."""


def child_process_alive(pid: int) -> bool:
    """Probe real (os.kill(pid, 0)): o processo existe? (pode ser zombie —
    quem decide é o par claim+filho via heartbeat_fn.)"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_process_group(pid: int) -> None:
    """Mata o grupo de processo do filho (spawn com start_new_session)."""
    try:
        os.killpg(pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        # já morto / sem permissão: o reclaim decide o destino
        return


def wait_with_heartbeat(
    proc: "subprocess.Popen",
    *,
    heartbeat_fn: Optional[Callable[[], bool]] = None,
    heartbeat_interval: float = 15.0,
    timeout_seconds: Optional[float] = None,
    kill_fn: Optional[Callable[[int], None]] = None,
    task_id: str = "?",
) -> int:
    """Espera ``proc`` renovando o claim a cada intervalo (heartbeat_fn).

    - ``heartbeat_fn``: chamado a cada intervalo enquanto o filho roda;
      deve devolver True quando o claim continua íntegro. None => sem
      heartbeat (comportamento legado: só timeout).
    - ``timeout_seconds``: deadline total; None => sem limite.
    - ``kill_fn``: como matar o filho ao abortar (default killpg).
    Devolve o returncode quando o processo termina sozinho.
    """
    if kill_fn is None:
        kill_fn = kill_process_group
    interval = max(float(heartbeat_interval), 0.05)
    deadline = (
        None if timeout_seconds is None
        else time.monotonic() + float(timeout_seconds)
    )
    while True:
        remaining = interval
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_and_reap(proc, kill_fn)
                raise HeartbeatDeadlineError(
                    f"worker for task '{task_id}' exceeded max runtime "
                    f"({timeout_seconds}s); process group killed."
                )
            remaining = min(remaining, interval)
        try:
            return proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass
        if heartbeat_fn is not None and not heartbeat_fn():
            _kill_and_reap(proc, kill_fn)
            raise HeartbeatLostError(
                f"worker for task '{task_id}' lost its claim (heartbeat "
                f"failed); process group killed."
            )


def _kill_and_reap(proc: "subprocess.Popen",
                   kill_fn: Callable[[int], None]) -> None:
    """Mata o filho e o reaps (sem warning de Popen não esperado)."""
    kill_fn(proc.pid)
    try:
        proc.wait(timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        return
