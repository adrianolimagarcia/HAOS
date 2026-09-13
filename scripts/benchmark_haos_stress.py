"""
Benchmark & Stress Test E2E - HAOS Task Engine, CPM/PIP Scheduler, ConcurrencyGuard & EventStore
Simula um DAG de 50 tarefas interdependentes com concorrência agressiva, falhas injetadas e failovers.
"""

import time
import tempfile
import threading
from pathlib import Path
from typing import List, Dict, Any

from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.execution.scheduler import (
    compute_deterministic_critical_path,
    compute_inherited_priorities,
    HAOSScheduler,
)
from hermes.platform.execution.backpressure import ConcurrencyGuard
from hermes.platform.execution.dispatcher import HAOSDispatcher
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.sink import EventStoreSink
from hermes.platform.evolution.analyzer import OuroborosAnalyzer
from hermes.platform.evolution.ledger import EvolutionLedger


def run_benchmark_stress_test() -> Dict[str, Any]:
    print("=== INICIANDO BENCHMARK & STRESS TEST E2E (50 TASKS DAG) ===")
    start_time = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        events_db = root / "events.db"
        kanban_db = root / "kanban.db"

        store = EventStore(events_db)
        sink = EventStoreSink(store)
        adapter = KanbanAdapter(kanban_db, event_sink=sink)

        # 1. Configura ConcurrencyGuard com limites estritos
        guard = ConcurrencyGuard(
            max_global_concurrency=8,
            provider_limits={"openai": 4, "anthropic": 4, "fallback": 2},
        )
        dispatcher = HAOSDispatcher(adapter, concurrency_guard=guard)

        # 2. Geração determinística de 50 Tarefas com topologia DAG
        # Camada 0: 5 tarefas iniciais independentes (T-0 a T-4)
        # Camada 1 a 8: 5 tarefas por camada dependendo da camada anterior
        # Camada 9: Tarefa final crítica T-49 dependendo de toda camada 8
        created_ids = []
        all_specs: List[TaskSpec] = []
        
        print("-> Gerando DAG com 50 nós e dependências cruzadas...")
        for i in range(50):
            deps = []
            if i >= 5:
                # Depende determinísticamente de 2 tarefas da camada anterior
                layer = i // 5
                prev_layer_start = (layer - 1) * 5
                dep1 = f"T-{prev_layer_start + (i % 5)}"
                dep2 = f"T-{prev_layer_start + ((i + 1) % 5)}"
                deps = [dep1, dep2]

            # Injeta prioridade extrema na tarefa T-49 para testar PIP
            priority = 100 if i == 49 else 20 + (i % 10)
            runtime = 15 if i % 2 == 0 else 5

            spec = TaskSpec(
                id=f"T-{i}",
                title=f"Node {i}",
                goal=f"Execute pipeline node {i}",
                priority=priority,
                max_runtime_minutes=runtime,
                requires_tasks=deps,
                workspace_type="scratch",
            )
            all_specs.append(spec)
            t_id = adapter.save_task(spec)
            created_ids.append(t_id)

        # 3. Teste Matemático do CPM e PIP sob carga total
        cpm_start = time.time()
        cp = compute_deterministic_critical_path(all_specs)
        inherited = compute_inherited_priorities(all_specs)
        cpm_duration = time.time() - cpm_start
        print(f"-> CPM & PIP computados em {cpm_duration*1000:.2f}ms. Nós críticos: {len(cp)}")
        assert len(cp) > 0, "Caminho crítico não pode ser vazio"
        assert inherited["T-0"] == 100.0, "Priority Inheritance falhou em propagar 100.0 para T-0"

        # 4. Stress de Concorrência Simulada com Múltiplas Threads
        print("-> Executando simulação de despacho paralelo com ConcurrencyGuard...")
        errors = []
        processed_tasks = []

        def worker_loop(thread_id: int):
            for _ in range(15):
                try:
                    executed = dispatcher.claim_tick(max_spawn=2)
                    if executed:
                        processed_tasks.extend(executed)
                except Exception as e:
                    errors.append(str(e))
                time.sleep(0.01)

        threads = [threading.Thread(target=worker_loop, args=(t,)) for t in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Erros durante execução paralela: {errors}"
        print(f"-> Ciclos de despacho finalizados. Tarefas executadas: {len(processed_tasks)}")
        assert guard.active_workers_count == 0, f"Vazamento de lock no guard: {guard.active_workers_count}"

        # 5. Injeta falhas categorizadas e verifica reação do Ouroboros Analyzer
        print("-> Injetando falhas estruturadas e auditando Ouroboros...")
        adapter.record_task_failure(created_ids[0], "LSP syntax failure on generated code", outcome="acceptance_failure")
        adapter.record_task_failure(created_ids[1], "Reviewer rejected patch format", outcome="review_rejection")

        analyzer = OuroborosAnalyzer()
        proposals = analyzer.analyze_execution_history(event_store=store)
        print(f"-> Propostas geradas pelo Ouroboros: {len(proposals)}")
        assert len(proposals) > 0, "Ouroboros deveria ter gerado proposta para as falhas injetadas"

        # Submete ao ledger
        ledger = EvolutionLedger(store)
        p_id = ledger.submit(proposals[0])
        assert ledger.pending()[0]["proposal_id"] == p_id
        ledger.decide(p_id, "approved", approver="sota-agent", rationale="Benchmark approval")
        assert len(ledger.pending()) == 0

        total_duration = time.time() - start_time
        print(f"=== BENCHMARK CONCLUÍDO COM SUCESSO EM {total_duration:.2f}s ===")
        return {
            "status": "PASS",
            "tasks_count": 50,
            "critical_path_length": len(cp),
            "cpm_calculation_time_ms": cpm_duration * 1000,
            "total_benchmark_duration_s": total_duration,
            "concurrency_guard_leak": guard.active_workers_count,
            "proposals_generated": len(proposals),
        }


if __name__ == "__main__":
    res = run_benchmark_stress_test()
    import pprint
    pprint.pprint(res)
