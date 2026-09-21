"""Gateway BotSpec Kanban adapter construction invariants."""

from pathlib import Path


def test_runner_kanban_adapter_uses_canonical_store(monkeypatch, tmp_path):
    from gateway import run

    seen = {}

    class FakeAdapter:
        def __init__(self):
            seen["home"] = run.get_hermes_home()

    monkeypatch.setattr("hermes.platform.tasks.kanban_adapter.KanbanAdapter", FakeAdapter)
    monkeypatch.setattr(run, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(run, "_profile_runtime_scope", lambda home: _scope(home))

    obj = object.__new__(run.GatewayRunner)
    obj._init_kanban_adapter()

    assert isinstance(obj.kanban_adapter, FakeAdapter)
    assert seen["home"] == tmp_path


class _scope:
    def __init__(self, home):
        self.home = Path(home)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
