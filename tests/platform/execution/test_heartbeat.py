"""D3 — Heartbeat/TTL do par (claim, filho) da lane agêntica.

Contratos (nunca snapshots):
(a) wait_with_heartbeat: processo que termina sozinho -> devolve rc (sem
    heartbeat_fn, sem timeout: comportamento legado do wait simples).
(b) heartbeat_fn False (claim perdido) -> HeartbeatLostError E o kill_fn
    recebeu o pid (par abortado).
(c) timeout_seconds estourado -> HeartbeatDeadlineError E kill_fn chamado;
    antes do deadline, sem kill.
(d) child_process_alive: pid do próprio processo -> True; pid inexistente
    -> False, sem exceção.
(e) E2E: Popen real de um filho que dorme pouco com heartbeat_fn True ->
    rc 0 devolvido e heartbeats contados > 0.
"""

import os
import subprocess
import sys
import unittest

from hermes.platform.execution.heartbeat import (
    HeartbeatLostError, HeartbeatDeadlineError,
    child_process_alive, wait_with_heartbeat,
)

_SLEEPER = (
    "import sys,time; time.sleep(float(sys.argv[1]) if len(sys.argv)>1 else 0.05)"
)


def _spawn(duration: float = 0.1):
    return subprocess.Popen(
        [sys.executable, "-c", _SLEEPER, str(duration)],
        start_new_session=True,
    )


class TestWaitWithHeartbeat(unittest.TestCase):
    def test_fast_exit_without_heartbeat_returns_rc(self):
        proc = _spawn(0.01)
        rc = wait_with_heartbeat(proc)
        self.assertEqual(rc, 0)

    def test_heartbeat_lost_kills_and_raises(self):
        killed = []
        proc = _spawn(5.0)  # dorme além do intervalo

        def kill_fn(pid):
            killed.append(pid)
            os.killpg(pid, 9)

        try:
            with self.assertRaises(HeartbeatLostError):
                wait_with_heartbeat(
                    proc,
                    heartbeat_fn=lambda: False,
                    heartbeat_interval=0.05,
                    kill_fn=kill_fn,
                )
        finally:
            try:
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.assertEqual(killed, [proc.pid])

    def test_deadline_kills_and_raises(self):
        killed = []
        proc = _spawn(5.0)

        def kill_fn(pid):
            killed.append(pid)
            os.killpg(pid, 9)

        try:
            with self.assertRaises(HeartbeatDeadlineError):
                wait_with_heartbeat(
                    proc,
                    timeout_seconds=0.1,
                    heartbeat_interval=0.05,
                    kill_fn=kill_fn,
                )
        finally:
            try:
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.assertEqual(killed, [proc.pid])

    def test_deadline_not_triggered_before(self):
        killed = []
        proc = _spawn(0.02)
        rc = wait_with_heartbeat(
            proc,
            timeout_seconds=10,
            heartbeat_interval=0.05,
            kill_fn=lambda pid: killed.append(pid),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(killed, [])

    def test_heartbeats_counted_while_alive(self):
        beats = []
        proc = _spawn(0.2)
        rc = wait_with_heartbeat(
            proc,
            heartbeat_fn=lambda: beats.append(1) or True,
            heartbeat_interval=0.03,
            timeout_seconds=10,
        )
        self.assertEqual(rc, 0)
        self.assertGreater(len(beats), 0)


class TestProcessAlive(unittest.TestCase):
    def test_alive_and_dead(self):
        self.assertTrue(child_process_alive(os.getpid()))
        self.assertFalse(child_process_alive(2**31 - 1))


if __name__ == "__main__":
    unittest.main()
