"""HAOSDispatcher — execução real pelos mecanismos canônicos do kernel (K1).

O upstream entrega dois modelos de execução, e o HAOS respeita ambos:

1. **Dispatcher tick** (`dispatch_once`): o Hermes (perfil) roda o dispatcher que
   claima/spawna cards `ready` sob lock single-writer, orçamento de concorrência,
   crash/stale/respawn. O HAOS injeta o ``spawn_fn`` (executa no workspace
   canônico e completa pela máquina de estados). Só faz sentido quando o board
   **não** está sob gate de perfis Hermes (senão assignee de lane é
   ``skipped_nonspawnable`` por design).

2. **Control-plane lane** (`claim_tick`): quando o Hermes roda com gate de
   perfis, uma lane de control-plane (ex.: ``haos/kilo``) **não** é um perfil —
   ela puxa os próprios cards via ``claim_task`` (modelo upstream de lanes como
   ``orion-cc``). O HAOS claima → resolve o workspace canônico → executa a lane
   → completa.

Em ambos, workspace e ciclo de vida são do kernel; o HAOS decide só o executor.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.execution.lane_executor import (
    LaneError,
    LaneWorker,
    get_lane_worker,
    lane_for_spec,
)
from hermes.platform.execution.contracts import (
    ContractGuard, ContractViolationError, TaskIOContract,
)
from hermes.platform.execution.preferences import agent_eligibility
from hermes.platform.execution.backpressure import ConcurrencyGuard

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_db_workspace as kbw


class HAOSDispatcher:
    def __init__(
        self,
        adapter: KanbanAdapter,
        *,
        board: Optional[str] = None,
        lane_worker: Optional[LaneWorker] = None,
        concurrency_guard: Optional[ConcurrencyGuard] = None,
    ):
        self.adapter = adapter
        self.board = board
        self.lane_worker = lane_worker
        self.concurrency_guard = concurrency_guard

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _lane_for_task(self, task_id: str) -> str:
        spec = (self.adapter.get_task(task_id) or {}).get("spec") or {}
        return lane_for_spec(spec)

    @staticmethod
    def _contract_guard_for(spec: Dict[str, Any]) -> Optional[ContractGuard]:
        """Reconstrói o ContractGuard persistido (None == sem contrato).

        ``task_contract`` viaja no spec JSON como dict round-trippable
        (KanbanAdapter._spec_dict); ausente/inválido ⇒ aceita-tudo."""
        raw = (spec or {}).get("task_contract")
        if not raw or not isinstance(raw, dict):
            return None
        return ContractGuard(TaskIOContract.from_dict(raw))

    @staticmethod
    def _accepts_heartbeat(worker: Any) -> bool:
        """Lane agêntica (HermesCliLaneWorker/Kilo) aceita heartbeat_fn?."""
        return (
            worker is not None
            and hasattr(worker, "execute")
            and "heartbeat_fn" in inspect.signature(worker.execute).parameters
        )

    def _run_and_complete_with_heartbeat(
        self,
        task_id: str,
        workspace: Path,
        *,
        hb_fn: Optional[Callable[[str], bool]] = None,
        heartbeat_interval: Optional[float] = None,
    ) -> None:
        """Executa e completa; repassa o heartbeat_fn p/ a lane quando ela o
        aceita (D3). Sem heartbeat o comportamento é o de ``_run_and_complete``."""
        spec = (self.adapter.get_task(task_id) or {}).get("spec") or {}
        lane = lane_for_spec(spec)
        worker = self.lane_worker or get_lane_worker(lane)
        if hb_fn is None or not self._accepts_heartbeat(worker):
            self._execute_card(task_id, workspace, spec, lane, worker, {})
            return
        kw = {"heartbeat_fn": hb_fn}
        if heartbeat_interval is not None:
            kw["heartbeat_interval"] = heartbeat_interval
        self._execute_card(task_id, workspace, spec, lane, worker, kw)

    def _execute_card(
        self,
        task_id: str,
        workspace: Path,
        spec: Dict[str, Any],
        lane: str,
        worker: Any,
        extra_kwargs: Dict[str, Any],
    ) -> None:
        """Guard (pre/post) + execução da lane + complete — caminho único."""
        guard = self._contract_guard_for(spec)
        if guard is not None:
            guard.gate_inputs(spec)
        out = worker.execute(task_id, workspace, spec, **extra_kwargs)
        if guard is not None:
            out = guard.gate_outputs(out)
        # Review Pipeline validation: if spec specifies review_stages, execute them
        spec_dict = spec if isinstance(spec, dict) else (getattr(spec, "to_dict", None)() if hasattr(spec, "to_dict") else {})
        if spec_dict.get("review_stages"):
            from hermes.platform.tasks.review_pipeline import ReviewPipeline
            from hermes.platform.tasks.spec import TaskSpec
            try:
                task_spec = TaskSpec.from_dict(spec_dict)
                pipeline = ReviewPipeline()
                verdicts = pipeline.run(
                    task_spec,
                    evidence=out.get("evidence") or {},
                    residual_risk=out.get("residual_risk") or [],
                    summary=out.get("summary") or "",
                )
                if any(not v.approved for v in verdicts):
                    reasons = [f"[{v.stage_id}] {v.rationale}" for v in verdicts if not v.approved]
                    raise ContractViolationError([f"Review pipeline rejected execution: {'; '.join(reasons)}"])
            except ContractViolationError:
                raise
            except Exception as ex:
                # Se houver erro de parsing ou validação e houver testes falhos na evidência
                tests_evidence = (out.get("evidence") or {}).get("tests") or {}
                if isinstance(tests_evidence, dict) and tests_evidence.get("failed", 0) > 0:
                    raise ContractViolationError([f"Review pipeline failed with failing tests in evidence: {ex}"])

        # Check evidence for explicit test failures if present
        tests_evidence = (out.get("evidence") or {}).get("tests") or {}
        if isinstance(tests_evidence, dict) and tests_evidence.get("failed", 0) > 0:
            if spec_dict.get("review_stages"):
                raise ContractViolationError([f"Execution has failing tests ({tests_evidence['failed']}) under required review stages"])

        completed = self.adapter.complete_task(
            task_id,
            summary=out.get("summary") or "",
            artifacts=out.get("artifacts") or [],
            evidence=out.get("evidence") or {},
            residual_risk=out.get("residual_risk") or [],
        )
        if not completed:
            raise RuntimeError(f"complete_task failed for task {task_id}")

    def _run_and_complete(self, task_id: str, workspace: Path) -> None:
        spec = (self.adapter.get_task(task_id) or {}).get("spec") or {}
        lane = lane_for_spec(spec)
        worker = self.lane_worker or get_lane_worker(lane)
        self._execute_card(task_id, workspace, spec, lane, worker, {})

    # ------------------------------------------------------------------ #
    # modelo 1: spawn_fn injetado no dispatcher upstream
    # ------------------------------------------------------------------ #
    def _spawn_worker(self, task, workspace: str, board: Optional[str] = None) -> Optional[int]:
        """Executa o card no workspace canônico e completa. PID None = em processo."""
        self._run_and_complete(task.id, Path(workspace))
        return None

    def dispatch_tick(
        self,
        *,
        default_assignee: str = "haos-worker",
        max_spawn: Optional[int] = None,
        dry_run: bool = False,
        **dispatch_kw: Any,
    ):
        """Um tick do dispatcher canônico com spawn_fn do HAOS.

        Cards `ready` são claimados/spawnados pelo upstream sob o lock do board.
        Retorna o ``DispatchResult`` upstream (``result.spawned`` =
        ``(task_id, assignee, workspace)``). Cards de lane não-perfil viram
        ``skipped_nonspawnable`` quando o gate de perfis Hermes está ativo —
        use ``claim_tick`` nesse caso."""
        conn = self.adapter._connect()
        return kbd.dispatch_once(
            conn,
            spawn_fn=self._spawn_worker,
            default_assignee=default_assignee,
            max_spawn=max_spawn,
            dry_run=dry_run,
            board=self.board,
            **dispatch_kw,
        )

    # ------------------------------------------------------------------ #
    # modelo 2: control-plane da lane (claim próprio)
    # ------------------------------------------------------------------ #
    def claim_tick(
        self,
        *,
        worker_id: str = "haos-worker",
        ttl_seconds: Optional[int] = None,
        max_spawn: Optional[int] = None,
        heartbeat_interval: Optional[float] = None,
        heartbeat_fn: Optional[Callable[[str], bool]] = None,
    ) -> List[str]:
        """Puxa e executa cards `ready` como control-plane de lane.

        Para cada card (ordem prioridade desc, criação asc): claim atômico
        upstream (abre o run canônico), resolve/cria o workspace canônico
        (scratch/worktree), executa a lane e completa. Retorna os ids
        executados. Cards com parent pendente são rebaixados a `todo` pelo
        próprio claim_task.

        D3 (heartbeat/TTL): quando o worker aceita ``heartbeat_fn`` (lane
        agêntica real), o tick passa um renew do claim — o control-plane que
        DETÉM o claim renova enquanto o filho vive (intervalo default
        ``ttl_seconds/3`` ou 15s); par quebrado (heartbeat False) ou deadline
        => kill do filho + falha registrada com orçamento."""
        conn = self.adapter._connect()
        limit = max_spawn if max_spawn is not None else 1
        executed: List[str] = []
        for _ in range(limit):
            row = conn.execute(
                "SELECT id FROM tasks WHERE status = 'ready' AND claim_lock IS NULL "
                "ORDER BY priority DESC, created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                break

            # F2 / Phase 3 Admission Guard: se configurado, avalia capacidade ANTES do claim.
            # Se não houver capacidade global/provider/model, o card permanece ready (claim_lock NULL),
            # sem queimar retries de consecutive_failures nem abrir subprocessos.
            if self.concurrency_guard is not None:
                task_meta = self.adapter.get_task(row["id"]) or {}
                spec_meta = task_meta.get("spec") or {}
                adm = self.concurrency_guard.acquire(
                    row["id"],
                    provider_id=spec_meta.get("provider_id"),
                    model_id=spec_meta.get("model_profile"),
                )
                if not adm.allowed:
                    # Capacidade temporariamente esgotada; interrompe o tick sem claimar o card
                    break

            claimed = kb.claim_task(conn, row["id"], claimer=worker_id, ttl_seconds=ttl_seconds)
            if claimed is None:
                if self.concurrency_guard is not None:
                    self.concurrency_guard.release(row["id"])
                break
            self.adapter.record_run_start(claimed.id, run_id=claimed.current_run_id,
                                          worker_id=worker_id)
            try:
                try:
                    workspace = kbw.resolve_workspace(claimed, board=self.board)
                    kbw.set_workspace_path(conn, claimed.id, str(workspace))
                except (ValueError, OSError) as exc:
                    self.adapter.record_task_failure(
                        claimed.id,
                        f"workspace resolution failed: {exc}",
                        outcome="workspace_unresolvable",
                    )
                    continue
                # Emenda 8 (claim path): required_agents é allow-list de lanes —
                # card destinado a outro agente não roda nesta lane (falha rápida
                # com orçamento, como os demais outcomes de falha).
                spec = (self.adapter.get_task(claimed.id) or {}).get("spec") or {}
                lane = lane_for_spec(spec)
                eligible, _ = agent_eligibility(
                    spec.get("required_agents") or [],
                    spec.get("preferred_agents") or [],
                    lane,
                )
                if not eligible:
                    self.adapter.record_task_failure(
                        claimed.id,
                        f"lane/agent '{lane}' not in required_agents "
                        f"{list(spec.get('required_agents') or [])}",
                        outcome="agent_required_missing",
                    )
                    continue
                # D3: renova o claim enquanto o filho da lane vive. O renew usa o
                # MESMO worker_id do claim (claim_lock = worker_id) e só acontece
                # quando o worker aceita heartbeat_fn (lane agêntica real); senão,
                # comportamento legado (wait único + TTL upstream).
                hb_fn: Optional[Callable[[str], bool]] = None
                worker = self.lane_worker or get_lane_worker(lane)
                if heartbeat_fn is not None or self._accepts_heartbeat(worker):
                    if heartbeat_fn is None:
                        hb_fn = lambda tid: self.adapter.heartbeat(tid, worker_id=worker_id)
                    else:
                        hb_fn = heartbeat_fn
                    if heartbeat_interval is None:
                        heartbeat_interval = max(float(ttl_seconds or 45) / 3.0, 5.0)
                try:
                    self._run_and_complete_with_heartbeat(
                        claimed.id, workspace, hb_fn=hb_fn,
                        heartbeat_interval=heartbeat_interval,
                    )
                except (LaneError, ContractViolationError, OSError) as exc:
                    # Hardening (Fase 1): falha da lane NÃO deixa o card preso em
                    # 'running' até o TTL — o orçamento do kernel (release_claim +
                    # consecutive_failures + auto-block no limite) decide o destino.
                    outcome = ("contract_violation" if isinstance(exc, ContractViolationError)
                               else "worker_crash")
                    self.adapter.record_task_failure(claimed.id, str(exc), outcome=outcome)
                    break  # falha sistêmica da lane: pare o tick (cards seguintes
                    # esperam o próximo tick; cada card tem seu próprio orçamento)
                executed.append(claimed.id)
            finally:
                if self.concurrency_guard is not None:
                    self.concurrency_guard.release(claimed.id)
        return executed
