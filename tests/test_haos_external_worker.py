"""Contract tests for HAOS External Worker Dispatcher (DSH & ACP)."""

from hermes.platform.workers.external import resolve_external_worker, ExternalWorkerSpec


def test_resolve_external_worker_dsh():
    task_context = {"task_id": "TSK-101", "workspace": "/tmp/proj", "title": "Implement feature X"}
    spec = resolve_external_worker("dsh", task_context)
    assert spec is not None
    assert spec.kind == "dsh"
    assert "exec" in spec.args
    assert "--workdir" in spec.args
    assert "/tmp/proj" in spec.args
    assert spec.env_vars.get("HAOS_EXTERNAL_WORKER") == "dsh"


def test_resolve_external_worker_acp():
    task_context = {"task_id": "TSK-202", "workspace": "/tmp/proj", "title": "Refactor module"}
    spec = resolve_external_worker("acp", task_context)
    assert spec is not None
    assert spec.kind == "acp"
    assert "--task" in spec.args
    assert "TSK-202" in spec.args


def test_resolve_external_worker_native_fallback():
    task_context = {"task_id": "TSK-303", "workspace": "/tmp/proj"}
    # Assignees matching regular profiles return None so native hermes worker is used
    assert resolve_external_worker("default", task_context) is None
    assert resolve_external_worker("coder", task_context) is None
    assert resolve_external_worker("architect", task_context) is None
