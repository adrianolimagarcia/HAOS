"""Testes de contrato do P10 — run contract nas missões longas do kanban.

Interpretação documentada (a spec só diz "run contract nas missões longas do
kanban"; o objetivo detalhado não existe in-repo): o TaskRun — a foto do que
aconteceu numa tentativa concreta — tem que ser CONSISTENTE como contrato, e
o caminho de missão longa (claim → heartbeat → end) nunca pode corrompê-lo:

  * ``TaskRun.validate_contract(now, max_idle_seconds)`` — verificação
    determinística de invariantes (status ∈ {running, ended}; started_at > 0;
    heartbeat_at nunca antes de started_at; ended ⇒ exit_reason presente;
    running com heartbeat velho/ausente além do ``max_idle_seconds`` ⇒ o run
    está preso — violação), devolvendo a lista de violações (vazia = válido);
  * ``TaskRun.end()`` — terminal idempotente: o PRIMEIRO exit_reason vence
    (um run encerrado não muda de causa depois);
  * adapter — depois que um run termina, ``heartbeat()`` não pode mais
    "tocar" o snapshot (missão longa não revive run encerrado).

Nenhum teste lê texto de código-fonte; nenhum é change-detector.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes.platform.execution.runs import TaskRun
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.tasks.spec import TaskSpec


def _run(**kw) -> TaskRun:
    base = dict(
        task_id="T-X", started_at=100.0, heartbeat_at=150.0,
        status="running", exit_reason=None,
    )
    base.update(kw)
    return TaskRun(**base)


class TestTaskRunContractValidate(unittest.TestCase):
    def test_running_valido_sem_violacoes(self):
        self.assertEqual(_run().validate_contract(now=200.0, max_idle_seconds=60), [])

    def test_status_invalido_e_violacao(self):
        violations = _run(status="limbo").validate_contract()
        self.assertTrue(any("status" in v for v in violations))

    def test_requer_started_at_positivo(self):
        violations = _run(started_at=0.0).validate_contract()
        self.assertTrue(any("started_at" in v for v in violations))

    def test_heartbeat_nunca_antes_do_start(self):
        violations = _run(started_at=200.0, heartbeat_at=100.0).validate_contract()
        self.assertTrue(any("heartbeat_at" in v for v in violations))

    def test_ended_requer_exit_reason(self):
        violations = _run(status="ended", exit_reason=None).validate_contract()
        self.assertTrue(any("exit_reason" in v for v in violations))

    def test_ended_com_exit_reason_valido(self):
        self.assertEqual(
            _run(status="ended", exit_reason="accepted").validate_contract(), []
        )

    def test_running_com_heartbeat_velho_e_preso(self):
        violations = _run(heartbeat_at=100.0).validate_contract(now=200.0, max_idle_seconds=30)
        self.assertTrue(any("stale" in v.lower() or "idle" in v.lower() for v in violations))

    def test_running_com_heartbeat_recente_ok(self):
        self.assertEqual(
            _run(heartbeat_at=190.0).validate_contract(now=200.0, max_idle_seconds=30), []
        )

    def test_running_sem_heartbeat_com_start_velho_e_preso(self):
        violations = _run(heartbeat_at=None, started_at=100.0).validate_contract(
            now=200.0, max_idle_seconds=30,
        )
        self.assertTrue(any("stale" in v.lower() or "idle" in v.lower() for v in violations))

    def test_running_sem_heartbeat_com_start_recente_ok(self):
        self.assertEqual(
            _run(heartbeat_at=None, started_at=195.0).validate_contract(
                now=200.0, max_idle_seconds=30,
            ),
            [],
        )

    def test_sem_max_idle_nao_acusa_preso(self):
        # Sem a janela de idle, um run running não é "preso" por tempo.
        self.assertEqual(
            _run(heartbeat_at=100.0).validate_contract(now=9999.0, max_idle_seconds=None), []
        )

    def test_end_idempotente_primeiro_exit_reason_vence(self):
        run = _run()
        run.end("accepted")
        run.end("review_rejected")  # segundo fim é no-op
        self.assertEqual(run.status, "ended")
        self.assertEqual(run.exit_reason, "accepted")


class TestMissaoLongaNuncaReviveRunEncerrado(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "kanban.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_heartbeat_apos_termino_nao_toca_o_snapshot(self):
        adapter = KanbanAdapter(self.db)
        spec = TaskSpec(id="T-200", title="Long Task", goal="Long mission")
        upstream = adapter.save_task(spec, status="READY")
        self.assertTrue(adapter.claim_task("T-200", "worker-1"))

        run = adapter._load_run(upstream)
        self.assertIsNotNone(run)
        self.assertEqual(run.status, "running")
        self.assertIsNone(run.exit_reason)
        adapter.heartbeat("T-200", "worker-1")
        run = adapter._load_run(upstream)
        self.assertEqual(run.status, "running")

        adapter.complete_task("T-200", summary="done")
        run = adapter._load_run(upstream)
        self.assertEqual(run.status, "ended")
        self.assertEqual(run.exit_reason, "accepted")
        ended_heartbeat = run.heartbeat_at

        # Heartbeat depois de encerrado: o claim upstream falha (e mesmo que
        # o canal devolva False, o snapshot não pode ser reanimado).
        adapter.heartbeat("T-200", "worker-1")
        run_after = adapter._load_run(upstream)
        self.assertEqual(run_after.status, "ended")
        self.assertEqual(run_after.exit_reason, "accepted")
        self.assertEqual(run_after.heartbeat_at, ended_heartbeat)

    def test_run_encerrado_valida_contrato_ok(self):
        adapter = KanbanAdapter(self.db)
        spec = TaskSpec(id="T-201", title="Short Task", goal="Finish")
        upstream = adapter.save_task(spec, status="READY")
        adapter.claim_task("T-201", "worker-1")
        adapter.complete_task("T-201", summary="done")
        run = adapter._load_run(upstream)
        self.assertIsNotNone(run)
        self.assertEqual(
            run.validate_contract(now=time_now(), max_idle_seconds=3600), []
        )


def time_now() -> float:
    import time
    return time.time()


if __name__ == "__main__":
    unittest.main()