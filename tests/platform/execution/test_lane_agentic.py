"""HermesCliLaneWorker — contrato da lane agêntica real (Fase 1).

Invariantes (contratos de comportamento, nunca snapshots):
(a) available(): fake executável -> True; comando inexistente -> False.
(b) execute() sucesso: retorna dict LaneWorker (status COMPLETED, lane,
    summary, evidence com pid>0/returncode/workspace); escreve .haos/spec.json
    antes do spawn; argv tem "chat"/"-q"; cwd == workspace; TERMINAL_CWD ==
    workspace; o env NÃO carrega HERMES_KANBAN_TASK/DB/BOARD (o filho não pode
    completar o card — double-complete excluído por construção).
(c) rc != 0 sem result.json -> LaneError.
(d) rc == 0 com result.json inválido -> LaneError.
(e) E2E: claim_tick com lane_worker=HermesCliLaneWorker(fake) completa o card
    canônico via control-plane (dispatcher._run_and_complete), sem chaves.
(f) L2: o probe --help do available() roda UMA vez por instância (cacheado).
(g) L4: perfil default herda a HERMES_HOME do processo; HAOS_HERMES_PROFILE
    vira -p <profile> + HERMES_HOME resolvida do perfil; perfil inexistente
    -> LaneError antes do spawn.
"""

import json
import os
import tempfile
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
    os.chmod(_FAKE, 0o755)  # shebang executável p/ spawn direto do peer
except OSError:
    pass


class TestLaneAgentic(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env_kanban = os.environ.get("HERMES_KANBAN_HOME")
        self._env_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
        os.environ["HERMES_HOME"] = str(root / "hermes-home")
        self.root = root
        self.db = root / "kanban.db"
        self.adapter = KanbanAdapter(self.db)
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

    # helpers -----------------------------------------------------------
    def _run_worker(self, workspace: Path, spec: dict, mode: str = "ok",
                    fake_log: Path | None = None):
        env = dict(os.environ)
        env["HAOS_FAKE_LOG"] = str(fake_log or (self.root / "fake.log"))
        env["HAOS_FAKE_MODE"] = mode
        self.worker.hermes_command = _FAKE
        self.worker._probe_result = None
        return env

    def test_available_gate(self):
        self.assertTrue(HermesCliLaneWorker(hermes_command=_FAKE).available())
        self.assertFalse(
            HermesCliLaneWorker(hermes_command="/nonexistent/hermes").available()
        )

    def test_execute_success_contract(self):
        workspace = self.root / "ws-t1"
        spec = {"id": "T-1", "required_capabilities": []}
        fake_log = self.root / "fake.log"
        env = self._run_worker(workspace, spec, fake_log=fake_log)
        old_env = dict(os.environ)
        os.environ.update(env)
        try:
            result = self.worker.execute("t_1", workspace, spec)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["lane"], "hermes")
        self.assertEqual(result["summary"], "done by fake")
        self.assertIsInstance(result["evidence"]["pid"], int)
        self.assertGreater(result["evidence"]["pid"], 0)
        self.assertEqual(result["evidence"]["returncode"], 0)
        self.assertEqual(result["evidence"]["workspace"], str(workspace))
        # spec.json foi escrito antes do spawn.
        self.assertTrue((workspace / ".haos" / "spec.json").exists())

        # A primeira linha do log é o probe --help do available(); o spawn
        # real é a última.
        logged = json.loads(fake_log.read_text(encoding="utf-8").splitlines()[-1])
        argv = logged["argv"]  # o fake loga sys.argv[1:] (sem o script)
        self.assertEqual(argv[0], "--cli")
        self.assertIn("--accept-hooks", argv)
        self.assertIn("chat", argv)
        self.assertIn("-q", argv)
        self.assertEqual(logged["cwd"], str(workspace.resolve()))
        self.assertEqual(logged["env"]["TERMINAL_CWD"], str(workspace.resolve()))
        # Lifecycle do Kanban purgado: o filho não pode completar o card.
        self.assertEqual(logged["env"]["HERMES_KANBAN_TASK"], "<unset>")
        self.assertEqual(logged["env"]["HERMES_KANBAN_RUN_ID"], "<unset>")
        self.assertEqual(logged["env"]["HERMES_KANBAN_CLAIM_LOCK"], "<unset>")
        self.assertEqual(logged["env"]["HERMES_KANBAN_DB"], "<unset>")
        self.assertEqual(logged["env"]["HERMES_KANBAN_BOARD"], "<unset>")
        self.assertEqual(logged["env"]["HERMES_KANBAN_WORKSPACE"], "<unset>")
        self.assertEqual(logged["env"]["HERMES_KANBAN_WORKSPACES_ROOT"], "<unset>")
        self.assertEqual(logged["env"]["HERMES_TUI"], "<unset>")
        # HERMES_HOME repassada (nunca removida do env do child).
        self.assertTrue(logged["env"]["HERMES_HOME"])

    def test_exit_nonzero_raises_lane_error(self):
        workspace = self.root / "ws-t2"
        env = self._run_worker(workspace, {"id": "T-2"}, mode="exit_nonzero")
        old_env = dict(os.environ)
        os.environ.update(env)
        try:
            with self.assertRaises(LaneError):
                self.worker.execute("t_2", workspace, {"id": "T-2"})
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_exit_zero_with_invalid_json_raises_lane_error(self):
        workspace = self.root / "ws-t3"
        env = self._run_worker(workspace, {"id": "T-3"}, mode="bad_json")
        old_env = dict(os.environ)
        os.environ.update(env)
        try:
            with self.assertRaises(LaneError):
                self.worker.execute("t_3", workspace, {"id": "T-3"})
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_claim_tick_e2e_with_fake_lane_worker(self):
        spec = TaskSpec(id="T-E2E", title="Agentic task", goal="g",
                        workspace_type="scratch")
        tid = self.adapter.save_task(spec, status="READY")

        fake_log = self.root / "fake.log"
        env = self._run_worker(self.root / "ws", {}, fake_log=fake_log)
        old_env = dict(os.environ)
        os.environ.update(env)
        try:
            dispatcher = HAOSDispatcher(
                self.adapter, lane_worker=self.worker
            )
            executed = dispatcher.claim_tick(max_spawn=1)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

        self.assertEqual(executed, [tid])
        self.assertEqual(self.adapter.get_task(tid)["status"], "done")
        result = self.adapter.get_result(tid)
        self.assertEqual(result.evidence["lane"], "hermes")
        self.assertIsInstance(result.evidence["pid"], int)
        run = self.adapter.get_run(tid)
        self.assertEqual(run.status, "ended")


    def test_available_probe_cached_once(self):
        # L2: o gate available() roda o probe --help UMA única vez por
        # instância; available() repetido e execute() são cache hits.
        fake_log = self.root / "probe-cache.log"
        old_env = dict(os.environ)
        os.environ["HAOS_FAKE_LOG"] = str(fake_log)
        os.environ["HAOS_FAKE_MODE"] = "ok"
        worker = HermesCliLaneWorker(hermes_command=_FAKE)
        try:
            self.assertTrue(worker.available())   # 1ª chamada: probe roda
            self.assertTrue(worker.available())   # cache hit — sem re-probe
            workspace = self.root / "ws-probe"
            result = worker.execute("t_probe", workspace, {"id": "T-probe"})
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        self.assertEqual(result["status"], "COMPLETED")
        lines = fake_log.read_text(encoding="utf-8").splitlines()
        help_runs = [l for l in lines if json.loads(l)["argv"][:1] == ["--help"]]
        self.assertEqual(len(help_runs), 1)  # probe único cacheado
        self.assertEqual(len(lines), 2)      # probe + spawn real do worker

    def test_profile_default_inherits_parent_home(self):
        # L4 default: sem HAOS_HERMES_PROFILE o child roda SEM -p e o argv
        # começa no contrato --cli; a HERMES_HOME do processo é repassada.
        workspace = self.root / "ws-prof-default"
        fake_log = self.root / "prof-default.log"
        old_env = dict(os.environ)
        os.environ["HAOS_FAKE_LOG"] = str(fake_log)
        os.environ["HAOS_FAKE_MODE"] = "ok"
        os.environ.pop("HAOS_HERMES_PROFILE", None)
        worker = HermesCliLaneWorker(hermes_command=_FAKE)
        try:
            result = worker.execute("t_pd", workspace, {"id": "T-PD"})
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        self.assertIsNone(worker.profile)
        logged = json.loads(fake_log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(logged["argv"][0], "--cli")
        self.assertNotIn("-p", logged["argv"])
        self.assertTrue(logged["env"]["HERMES_HOME"])

    def test_profile_override_env_sets_argv_and_home(self):
        # L4 override: HAOS_HERMES_PROFILE vira -p <profile> no argv e a
        # HERMES_HOME do child é a resolvida do perfil (resolve_profile_env).
        profile_dir = self.root / "hermes-home" / "profiles" / "worker1"
        profile_dir.mkdir(parents=True, exist_ok=True)
        workspace = self.root / "ws-prof"
        fake_log = self.root / "prof.log"
        old_env = dict(os.environ)
        os.environ["HAOS_FAKE_LOG"] = str(fake_log)
        os.environ["HAOS_FAKE_MODE"] = "ok"
        os.environ["HAOS_HERMES_PROFILE"] = "worker1"
        worker = HermesCliLaneWorker(hermes_command=_FAKE)
        try:
            self.assertEqual(worker.profile, "worker1")
            result = worker.execute("t_p", workspace, {"id": "T-P"})
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        self.assertEqual(result["status"], "COMPLETED")
        logged = json.loads(fake_log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(logged["argv"][:2], ["-p", "worker1"])
        self.assertEqual(logged["env"]["HERMES_HOME"], str(profile_dir))

    def test_profile_missing_raises_lane_error(self):
        # L4 fail-closed: perfil inexistente -> LaneError antes do spawn.
        workspace = self.root / "ws-ghost"
        old_env = dict(os.environ)
        os.environ["HAOS_FAKE_LOG"] = str(self.root / "ghost.log")
        os.environ["HAOS_FAKE_MODE"] = "ok"
        os.environ["HAOS_HERMES_PROFILE"] = "ghost"
        worker = HermesCliLaneWorker(hermes_command=_FAKE)
        try:
            with self.assertRaises(LaneError):
                worker.execute("t_g", workspace, {"id": "T-G"})
        finally:
            os.environ.clear()
            os.environ.update(old_env)


if __name__ == "__main__":
    unittest.main()
