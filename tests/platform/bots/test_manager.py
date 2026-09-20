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
