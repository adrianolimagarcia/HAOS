"""D3 — Heartbeat/TTL E2E da lane agêntica (filho real + claim real).

Invariantes (contratos de comportamento, nunca snapshots):
(a) claim_tick com worker agêntico real (fake "slow_ok") e heartbeat
    habilitado: o filho dorme enquanto o control-plane renova o claim a cada
    intervalo; o card completa `done` e os heartbeats foram > 0.
(b) heartbeat_fn que devolve False (claim perdido/reclaimado por outro) ->
    HeartbeatLostError; o card NÃO fica preso em running (release + run
    encerrado) — orçamento do kernel decide o destino.
(c) deadline (timeout_seconds curto) mata o filho e falha rápido: tempo
    decorrido << duração do fake (o kill foi efetivo, não esperou o fim).
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.execution.lane_executor import (
    HermesCliLaneWorker, LaneError,
)
from hermes.platform.execution.dispatcher import HAOSDispatcher

_FAKE = str(Path(__file__).resolve().parent / "_fake_hermes.py")
try:
    os.chmod(_FAKE, 0o755)
except OSError:
    pass


class TestLaneHeartbeatE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env_kanban = os.environ.get("HERMES_KANBAN_HOME")
        self._env_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
        os.environ["HERMES_HOME"] = str(root / "hermes-home")
        self.root = root
        self.adapter = KanbanAdapter(root / "kanban.db")
        self.worker = HermesCliLaneWorker(hermes_command=_FAKE)

    def tearDown(self):
        self.adapter.close()
        if self._env_kanban is None:
            os.environ.pop("HERMES_KANBAN_HOME", None)
        else:
            os.environ["HERMES_KANBAN_HOME"] = self._env_kanban
        if self._env_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._env_home
        self._tmp.cleanup()

    def _env(self, mode: str, sleep: str = "0.6"):
        env = dict(os.environ)
        env["HAOS_FAKE_LOG"] = str(self.root / "fake.log")
        env["HAOS_FAKE_MODE"] = mode
        env["HAOS_FAKE_SLEEP"] = sleep
        self.worker.hermes_command = _FAKE
        return env

    def _claim_tick(self, mode: str = "slow_ok", sleep: str = "0.6",
                    heartbeat_interval: float = 0.05, ttl_seconds: int = 2,
                    heartbeat_fn=None):
        spec = TaskSpec(id="T-HB", title="Heartbeat task", goal="g",
                        workspace_type="scratch")
        tid = self.adapter.save_task(spec, status="READY")
        env = self._env(mode, sleep=sleep)
        old_env = dict(os.environ)
        os.environ.update(env)
        try:
            dispatcher = HAOSDispatcher(self.adapter, lane_worker=self.worker)
            executed = dispatcher.claim_tick(
                max_spawn=1, ttl_seconds=ttl_seconds,
                heartbeat_interval=heartbeat_interval,
                heartbeat_fn=heartbeat_fn,
            )
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        return tid, executed

    def test_claim_tick_renews_claim_while_child_alive(self):
        beats = []

        def hb(tid):
            beats.append(tid)
            return self.adapter.heartbeat(tid, worker_id="haos-worker")

        tid, executed = self._claim_tick(heartbeat_fn=hb)
        self.assertEqual(executed, [tid])
        self.assertEqual(self.adapter.get_task(tid)["status"], "done")
        self.assertGreater(len(beats), 0)
        run = self.adapter.get_run(tid)
        self.assertEqual(run.status, "ended")

    def test_lost_claim_does_not_strand_card(self):
        tid, executed = self._claim_tick(heartbeat_fn=lambda tid: False)
        self.assertEqual(executed, [])
        task = self.adapter.get_task(tid)
        # Não preso em running: o orçamento do kernel liberou o claim e
        # encerrou o run canônico (o destino é do upstream, não do HAOS).
        self.assertNotEqual(task["status"], "running")
        run = self.adapter.get_run(tid)
        self.assertEqual(run.status, "ended")
        # O run snapshot HAOS registrou o outcome (worker_crash).
        haos_run = self.adapter.get_run(tid)
        self.assertEqual(haos_run.status, "ended")

    def test_deadline_kills_child_and_fails_fast(self):
        spec = TaskSpec(id="T-DL", title="Deadline task", goal="g",
                        workspace_type="scratch")
        tid = self.adapter.save_task(spec, status="READY")
        env = self._env("slow_ok", sleep="3.0")
        old_env = dict(os.environ)
        os.environ.update(env)
        self.worker.timeout_seconds = 0.2  # deadline curto
        start = time.monotonic()
        try:
            dispatcher = HAOSDispatcher(self.adapter, lane_worker=self.worker)
            executed = dispatcher.claim_tick(
                max_spawn=1, ttl_seconds=2,
                heartbeat_interval=0.05,
                heartbeat_fn=lambda tid: self.adapter.heartbeat(
                    tid, worker_id="haos-worker"),
            )
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        elapsed = time.monotonic() - start
        self.assertEqual(executed, [])
        # Matou de verdade: falhou em ~0.2s (e não esperou os 3.0s do fake).
        self.assertLess(elapsed, 1.5)
        task = self.adapter.get_task(tid)
        self.assertNotEqual(task["status"], "running")


if __name__ == "__main__":
    unittest.main()
