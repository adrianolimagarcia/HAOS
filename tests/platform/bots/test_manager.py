from hermes.platform.bots import BotSpec, BotSpecManager
from hermes.platform.observability.event_store import EventStore

def test_bot_specs_replay_and_submit():
    store = EventStore()
    mgr = BotSpecManager(store)
    bot = mgr.register(BotSpec(id="coder", name="Coder", task_defaults={"posture": "implementer"}, capabilities=["code"]))
    assert mgr.get("coder") == bot
    task = mgr.submit("coder", "Fix bug", priority=90)
    assert task.goal == "Fix bug" and task.priority == 90 and task.posture == "implementer"
    mgr.update(BotSpec(id="coder", name="Coder v2"))
    assert mgr.get("coder").name == "Coder v2"
    mgr.delete("coder")
    assert mgr.get("coder") is None

def test_create_cron_job_uses_canonical_api(monkeypatch):
    calls = []
    monkeypatch.setattr("cron.scheduler.create_job_with_scheduler_registration", lambda **kwargs: calls.append(kwargs) or {"id": "job"})
    mgr = BotSpecManager(EventStore())
    mgr.register(BotSpec(id="bot", name="Bot", routines={"daily": {"prompt": "run daily"}}))
    assert mgr.create_cron_job("bot", "daily", "0 9 * * *")["id"] == "job"
    assert calls[0]["bot_id"] == "bot" and calls[0]["routine"] == "daily"

def test_duplicate_and_unknown():
    mgr = BotSpecManager(EventStore())
    mgr.register(BotSpec(id="x", name="X"))
    try: mgr.register(BotSpec(id="x", name="X")); assert False
    except ValueError: pass
    try: mgr.submit("missing", "goal"); assert False
    except KeyError: pass

def test_submit_to_dispatcher_persists_canonical_task(tmp_path):
    from hermes.platform.tasks.kanban_adapter import KanbanAdapter
    mgr = BotSpecManager(EventStore())
    mgr.register(BotSpec(id="worker", name="Worker", routines={"fix": {"version": 2, "task_defaults": {"priority": 80}}}))
    adapter = KanbanAdapter(tmp_path / "kanban.db")
    task_id = mgr.submit_to_dispatcher("worker", "fix", "Repair it", adapter)
    assert task_id.startswith("t_")
    stored = adapter.get_task(task_id)
    assert stored["spec"]["goal"] == "Repair it"
    assert stored["spec"]["priority"] == 80
    assert mgr.run_history("worker", "fix")[-1] == {
        "bot_id": "worker", "routine": "fix", "run_id": task_id, "status": "submitted"
    }
    adapter.close()


def test_worker_lifecycle_e2e_submitted_claimed_running_completed(tmp_path):
    """Exercise the real BotSpec -> Kanban adapter lifecycle seam."""
    from hermes.platform.tasks.kanban_adapter import KanbanAdapter

    store = EventStore()
    mgr = BotSpecManager(store)
    mgr.register(BotSpec(id="worker", name="Worker", routines={"fix": {}}))
    adapter = KanbanAdapter(tmp_path / "lifecycle.db")
    task_id = mgr.submit_to_dispatcher("worker", "fix", "Repair it", adapter)
    assert mgr.run_state("worker", task_id)["status"] == "submitted"

    assert adapter.claim_task(task_id, worker_id="e2e-worker")
    mgr.record_run("worker", "fix", task_id, "claimed", worker_id="e2e-worker")
    mgr.record_run("worker", "fix", task_id, "running")
    assert adapter.complete_task(task_id, summary="repaired")
    mgr.record_run("worker", "fix", task_id, "completed")
    assert [event["status"] for event in mgr.run_history("worker", "fix")] == [
        "submitted", "claimed", "running", "completed"
    ]
    adapter.close()


def test_worker_lifecycle_terminal_failure_cancelled_blocked():
    mgr = BotSpecManager(EventStore())
    mgr.register(BotSpec(id="worker", name="Worker", routines={"fix": {}}))
    for run_id, status in (("failed-run", "failed"), ("cancelled-run", "cancelled"), ("blocked-run", "blocked")):
        mgr.record_run("worker", "fix", run_id, status)
    assert [item["status"] for item in mgr.run_history("worker", "fix")] == [
        "failed", "cancelled", "blocked"
    ]


def test_submit_to_dispatcher_records_failure_without_swallowing_error():
    class FailingAdapter:
        def save_task(self, task):
            raise RuntimeError("dispatcher unavailable")

    store = EventStore()
    mgr = BotSpecManager(store)
    mgr.register(BotSpec(id="worker", name="Worker", routines={"fix": {}}))
    try:
        mgr.submit_to_dispatcher("worker", "fix", "Repair it", FailingAdapter())
        assert False
    except RuntimeError as exc:
        assert str(exc) == "dispatcher unavailable"
    assert mgr.run_history("worker", "fix")[-1]["status"] == "dispatch_failed"


def test_submit_to_dispatcher_carries_yolo_override_onto_the_spec(tmp_path):
    """O override yolo_mode das superfícies de operador chega ao spec persistido.

    Caminho compartilhado pelo dashboard (POST /bots/{id}/trigger|submit) e pelo
    webhook assinado: o kwarg entra em ``overrides``, vence o ``task_defaults``
    do BotSpec e é persistido — é o que a lane lê para decidir o ``--yolo``.
    """
    from hermes.platform.tasks.kanban_adapter import KanbanAdapter

    adapter = KanbanAdapter(tmp_path / "kanban.db")
    try:
        mgr = BotSpecManager(EventStore())
        # task_defaults diz o contrário de propósito: a política do operador vence.
        mgr.register(BotSpec(id="worker", name="Worker",
                             routines={"fix": {"task_defaults": {"yolo_mode": False}}}))

        yolo_id = mgr.submit_to_dispatcher("worker", "fix", "Repair it", adapter, yolo_mode=True)
        gated_id = mgr.submit_to_dispatcher("worker", "fix", "Repair it", adapter)

        assert adapter.get_task(yolo_id)["spec"]["yolo_mode"] is True
        assert adapter.get_task(gated_id)["spec"]["yolo_mode"] is False
    finally:
        adapter.close()


def test_submit_to_dispatcher_ledger_records_trigger_event_only_when_truthy(tmp_path):
    """Proveniência do disparo: chave no ledger de runs, e só quando informada.

    ``trigger_event`` é parâmetro explícito (não ``**overrides``): com valor
    truthy ele vira a chave ``trigger_event`` do run_history; sem o parâmetro o
    registro mantém exatamente o shape canônico de 4 chaves, para que os callers
    existentes não vejam o ledger mudar de forma. O valor é sempre gravado como
    string: o ledger é serializado em JSON e o dado vem de fora, sem validação
    de tipo na origem.
    """
    from hermes.platform.tasks.kanban_adapter import KanbanAdapter

    adapter = KanbanAdapter(tmp_path / "kanban.db")
    try:
        mgr = BotSpecManager(EventStore())
        mgr.register(BotSpec(id="worker", name="Worker", routines={"fix": {}}))

        push_id = mgr.submit_to_dispatcher("worker", "fix", "Repair it", adapter, trigger_event="push")
        assert mgr.run_history("worker", "fix")[-1] == {
            "bot_id": "worker", "routine": "fix", "run_id": push_id, "status": "submitted",
            "trigger_event": "push",
        }

        # O valor chega de fora sem validação de tipo (o webhook o tira do corpo da
        # requisição): o ledger é JSON e o resto do sistema lê a chave como nome de
        # evento, então o contrato é gravar sempre string.
        for hostile in ({"a": 1}, {1, 2}):
            mgr.submit_to_dispatcher("worker", "fix", "Repair it", adapter, trigger_event=hostile)
            assert isinstance(mgr.run_history("worker", "fix")[-1]["trigger_event"], str)

        plain_id = mgr.submit_to_dispatcher("worker", "fix", "Repair it", adapter)
        record = mgr.run_history("worker", "fix")[-1]
        assert record == {
            "bot_id": "worker", "routine": "fix", "run_id": plain_id, "status": "submitted",
        }
        assert "trigger_event" not in record
    finally:
        adapter.close()


def test_submit_to_dispatcher_never_leaks_trigger_event_into_the_persisted_spec(tmp_path):
    """Proveniência do disparo não é campo do TaskSpec — e não pode virar um.

    Se ``trigger_event`` chegasse por ``**overrides`` ele entraria em
    ``TaskSpec(**values)``, que é um dataclass fechado — chave desconhecida ali é
    ``TypeError`` e a submissão morreria antes de persistir. Como parâmetro
    explícito ele fica só no ledger; o spec no kanban continua limpo.

    Isto cobre ESTA chave nesta porta, não a classe inteira: qualquer outra chave
    arbitrária em ``**overrides`` de ``submit_to_dispatcher``/``submit_routine``/
    ``trigger_manual``/``submit`` continua chegando em ``TaskSpec(**values)``.
    """
    from hermes.platform.tasks.kanban_adapter import KanbanAdapter

    adapter = KanbanAdapter(tmp_path / "kanban.db")
    try:
        mgr = BotSpecManager(EventStore())
        mgr.register(BotSpec(id="worker", name="Worker", routines={"fix": {}}))

        task_id = mgr.submit_to_dispatcher("worker", "fix", "Repair it", adapter, trigger_event="push")

        stored = adapter.get_task(task_id)
        assert stored["spec"]["goal"] == "Repair it"
        assert "trigger_event" not in stored["spec"]
    finally:
        adapter.close()
