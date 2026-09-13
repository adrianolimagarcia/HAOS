"""A7 — executor agêntico da lane kilo (invariantes).

Contratos:
(a) KiloAgenticLaneWorker SÓ spawna o worker canônico quando o workspace é um
    worktree git (marcador ``.git``): sem marcador -> LaneError ANTES de o
    filho nascer (zero chamadas ao peer — log do fake não é criado).
(b) Com worktree git: o worker canônico roda (cwd=workspace, peer scriptado),
    spec.json é escrito em .haos e o resultado vem com lane "kilo".
(c) install_real_lane_workers troca hermes+kilo pelos agênticos reais quando o
    runtime é resolvable; sem runtime não troca nada (defaults preservados).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from hermes.platform.execution.lane_executor import (
    KiloAgenticLaneWorker, LaneError, LaneWorker, get_lane_worker,
    install_real_lane_workers, LANE_WORKERS, LANE_KILO, LANE_HERMES,
)

FAKE_HERMES = Path(__file__).resolve().with_name("_fake_hermes.py")

_GIT_SPEC = {"required_capabilities": ["git"],
             "workspace_type": "git_worktree"}


class TestKiloAgenticLane(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        (self.worktree / ".git").mkdir()  # marcador de worktree git
        self.log = self.root / "fake.log"
        self._prev_log = os.environ.get("HAOS_FAKE_LOG")
        os.environ["HAOS_FAKE_LOG"] = str(self.log)

    def tearDown(self):
        if self._prev_log is None:
            os.environ.pop("HAOS_FAKE_LOG", None)
        else:
            os.environ["HAOS_FAKE_LOG"] = self._prev_log
        self._tmp.cleanup()

    def _calls(self) -> int:
        if not self.log.exists():
            return 0
        return len(self.log.read_text(encoding="utf-8").splitlines())

    def test_executes_in_git_worktree(self):
        worker = KiloAgenticLaneWorker(hermes_command=str(FAKE_HERMES))
        result = worker.execute("K-1", self.worktree, _GIT_SPEC)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["lane"], "kilo")
        self.assertEqual(result["summary"], "done by fake")
        spec_file = self.worktree / ".haos" / "spec.json"
        self.assertTrue(spec_file.exists())
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        self.assertEqual(spec["required_capabilities"], ["git"])
        # Peer rodou exatamente a execução do worker canônico.
        self.assertGreaterEqual(self._calls(), 1)
        # Filho herdou TERMINAL_CWD = worktree (nunca o dir solto).
        lines = self.log.read_text(encoding="utf-8").splitlines()
        last = json.loads(lines[-1])
        self.assertEqual(last["env"]["TERMINAL_CWD"],
                         str(self.worktree.resolve()))
        self.assertEqual(last["cwd"], str(self.worktree))

    def test_refuses_loose_directory_before_spawn(self):
        loose = self.root / "loose"
        loose.mkdir()
        worker = KiloAgenticLaneWorker(hermes_command=str(FAKE_HERMES))
        with self.assertRaises(LaneError):
            worker.execute("K-2", loose, _GIT_SPEC)
        # Zero chamadas ao peer: nem o probe nem a execução nasceram.
        self.assertEqual(self._calls(), 0)

    def test_rc_nonzero_propagates(self):
        os.environ["HAOS_FAKE_MODE"] = "exit_nonzero"
        try:
            worker = KiloAgenticLaneWorker(hermes_command=str(FAKE_HERMES))
            with self.assertRaises(LaneError):
                worker.execute("K-3", self.worktree, _GIT_SPEC)
        finally:
            os.environ.pop("HAOS_FAKE_MODE", None)


class TestInstallRealLaneWorkers(unittest.TestCase):
    def test_installs_agentic_when_runtime_resolvable(self):
        snapshot = dict(LANE_WORKERS)
        try:
            installed = install_real_lane_workers(hermes_command=str(FAKE_HERMES))
            self.assertEqual(set(installed), {LANE_HERMES, LANE_KILO})
            self.assertIsInstance(get_lane_worker(LANE_KILO),
                                  KiloAgenticLaneWorker)
            self.assertNotIsInstance(get_lane_worker(LANE_KILO),
                                     type(snapshot[LANE_KILO]))
        finally:
            LANE_WORKERS.clear()
            LANE_WORKERS.update(snapshot)

    def test_no_runtime_keeps_defaults(self):
        snapshot = dict(LANE_WORKERS)
        try:
            installed = install_real_lane_workers(hermes_command="/nonexistent/hermes")
            self.assertEqual(installed, [])
            self.assertIs(get_lane_worker(LANE_KILO), snapshot[LANE_KILO])
        finally:
            LANE_WORKERS.clear()
            LANE_WORKERS.update(snapshot)


if __name__ == "__main__":
    unittest.main()
