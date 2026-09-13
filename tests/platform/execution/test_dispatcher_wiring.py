"""Wire + hardening da Fase 1 — contract guard no dispatcher (invariantes).

Contratos de comportamento:
(a) saída HARD (required ausente no evidence) -> complete_task NUNCA roda: o
    card volta a `ready` com o claim liberado e, no limite do orçamento do
    kernel (consecutive_failures >= DEFAULT_FAILURE_LIMIT), vira `blocked` —
    retry NUNCA ilimitado;
(b) entrada HARD (required ausente no spec) -> bloqueia ANTES do worker;
(c) SOFT (unknown_key) -> complete_task roda e o resultado carrega
    residual_risk "contract:unknown_key:…" + evidence["contract_violations"];
(d) persistência: task_contract viaja no spec JSON e o guard é reconstruído
    pelo dispatcher (round-trip).
"""

import os
import tempfile
import unittest
from pathlib import Path

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.execution.dispatcher import HAOSDispatcher
from hermes.platform.execution.lane_executor import LaneWorker, LaneError
from hermes.platform.execution.contracts import (
    ContractGuard, FieldContract, TaskIOContract,
)


class _BadOutputWorker(LaneWorker):
    """Sempre devolve um dict sem o campo que o contrato exige (HARD)."""

    name = "bad-output"
    calls = 0

    def available(self):
        return True

    def execute(self, task_id, workspace, spec):
        _BadOutputWorker.calls += 1
        return {"status": "COMPLETED", "summary": "s",
                "evidence": {"lane": "hermes"}, "artifacts": [], "residual_risk": []}


class _BoomWorker(LaneWorker):
    """Sempre levanta LaneError (crash da lane)."""

    name = "boom"

    def available(self):
        return True

    def execute(self, task_id, workspace, spec):
        raise LaneError("worker exploded")


class _SoftExtraWorker(LaneWorker):
    """Devolve evidence com chave extra não declarada (SOFT unknown_key)."""

    name = "soft-extra"

    def available(self):
        return True

    def execute(self, task_id, workspace, spec):
        return {"status": "COMPLETED", "summary": "feito",
                "evidence": {"lane": "hermes", "zz": 1},
                "artifacts": [], "residual_risk": []}


class _CountingWorker(LaneWorker):
    """Conta execuções (para provar que o pre-gate bloqueou antes do worker)."""

    name = "counting"
    calls = 0

    def available(self):
        return True

    def execute(self, task_id, workspace, spec):
        _CountingWorker.calls += 1
        return {"status": "COMPLETED", "summary": "s",
                "evidence": {"lane": "hermes"}, "artifacts": [], "residual_risk": []}


class TestDispatcherWiring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env_kanban = os.environ.get("HERMES_KANBAN_HOME")
        self._env_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
        os.environ["HERMES_HOME"] = str(root / "hermes-home")
        self.db = root / "kanban.db"
        self.adapter = KanbanAdapter(self.db)

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

    def _save(self, spec):
        return self.adapter.save_task(spec, status="READY")

    # ------------------------------------------------------------------ #
    def test_output_hard_violation_never_completes_and_budget_blocks(self):
        spec = TaskSpec(
            id="T-H1", title="Contrato", goal="g", workspace_type="scratch",
            task_contract=TaskIOContract(
                outputs=[FieldContract("executor", "str", required=True)],
            ),
        )
        tid = self._save(spec)
        dispatcher = HAOSDispatcher(self.adapter,
                                    lane_worker=_BadOutputWorker())
        executed = dispatcher.claim_tick(max_spawn=1)
        # 1ª falha: sem complete_task; claim liberado -> de volta a ready.
        self.assertEqual(executed, [])
        self.assertEqual(self.adapter.get_task(tid)["status"], "ready")
        run = self.adapter.get_run(tid)
        self.assertEqual(run.status, "ended")
        self.assertEqual(run.exit_reason, "contract_violation")
        # 2ª falha: orçamento do kernel estoura -> blocked (retry NUNCA sem fim).
        dispatcher.claim_tick(max_spawn=1)
        self.assertEqual(self.adapter.get_task(tid)["status"], "blocked")

    def test_input_hard_violation_blocks_before_worker(self):
        _CountingWorker.calls = 0
        spec = TaskSpec(
            id="T-H2", title="Contrato in", goal="g", workspace_type="scratch",
            task_contract=TaskIOContract(
                inputs=[FieldContract("zz_never", "str", required=True)],
            ),
        )
        tid = self._save(spec)
        dispatcher = HAOSDispatcher(self.adapter,
                                    lane_worker=_CountingWorker())
        executed = dispatcher.claim_tick(max_spawn=1)
        self.assertEqual(executed, [])
        self.assertEqual(_CountingWorker.calls, 0)  # nem chegou a executar
        self.assertEqual(self.adapter.get_task(tid)["status"], "ready")

    def test_lane_crash_releases_claim_and_closes_run(self):
        spec = TaskSpec(id="T-B1", title="Boom", goal="g",
                        workspace_type="scratch")
        tid = self._save(spec)
        dispatcher = HAOSDispatcher(self.adapter, lane_worker=_BoomWorker())
        executed = dispatcher.claim_tick(max_spawn=1)
        self.assertEqual(executed, [])
        self.assertEqual(self.adapter.get_task(tid)["status"], "ready")
        run = self.adapter.get_run(tid)
        self.assertEqual(run.status, "ended")
        self.assertEqual(run.exit_reason, "worker_crash")

    def test_soft_unknown_key_completes_with_annotation(self):
        spec = TaskSpec(
            id="T-S1", title="Soft", goal="g", workspace_type="scratch",
            task_contract=TaskIOContract(
                outputs=[FieldContract("lane", "str")],
                allow_extra_keys=False,
            ),
        )
        tid = self._save(spec)
        dispatcher = HAOSDispatcher(self.adapter,
                                    lane_worker=_SoftExtraWorker())
        executed = dispatcher.claim_tick(max_spawn=1)
        self.assertEqual(executed, [tid])
        self.assertEqual(self.adapter.get_task(tid)["status"], "done")
        result = self.adapter.get_result(tid)
        self.assertTrue(any(
            r.startswith("contract:unknown_key:") for r in result.residual_risk
        ))
        self.assertTrue(result.evidence["contract_violations"])

    def test_task_contract_round_trip_through_spec_json(self):
        contract = TaskIOContract(
            inputs=[FieldContract("goal", "str", required=True)],
            outputs=[FieldContract("lane", "str", required=True)],
            allow_extra_keys=False,
        )
        spec = TaskSpec(id="T-P1", title="P", goal="g",
                        workspace_type="scratch", task_contract=contract)
        tid = self._save(spec)
        persisted = self.adapter.get_task(tid)["spec"]["task_contract"]
        self.assertEqual(persisted["outputs"][0]["name"], "lane")
        guard = HAOSDispatcher._contract_guard_for(
            self.adapter.get_task(tid)["spec"]
        )
        self.assertIsInstance(guard, ContractGuard)
        self.assertEqual(guard.contract.outputs[0].name, "lane")
        self.assertFalse(guard.contract.allow_extra_keys)
        # Sem contrato -> guard None (aceita-tudo, aditivo).
        plain = TaskSpec(id="T-P2", title="P", goal="g",
                         workspace_type="scratch")
        tid2 = self._save(plain)
        self.assertIsNone(
            HAOSDispatcher._contract_guard_for(self.adapter.get_task(tid2)["spec"])
        )


if __name__ == "__main__":
    unittest.main()
