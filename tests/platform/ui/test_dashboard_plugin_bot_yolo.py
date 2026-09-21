"""Rotas de bot do dashboard oficial: missão do operador nasce em YOLO.

``POST /bots/{id}/trigger`` e ``POST /bots/{id}/submit`` criam a missão a partir
de uma ordem do operador, então passam ``yolo_mode=True`` ao dispatcher canônico
(``OPERATOR_YOLO_DEFAULT``). O worker é headless: sem isso a missão para num
prompt de aprovação que ninguém vê na superfície do dashboard.

Exercita as rotas REAIS com ``BotSpecManager`` e ``KanbanAdapter`` reais sobre um
data_dir temporário — o que se assere é o spec persistido no kanban canônico.
"""

import pytest

from hermes.platform.bots import BotSpec, BotSpecManager
from hermes.platform.observability.event_store import EventStore
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.ui.dashboard_plugin import plugin_api


@pytest.fixture
def wired(tmp_path, monkeypatch):
    if not getattr(plugin_api, "_HAS_FASTAPI", False):
        pytest.skip("fastapi indisponível: as rotas do plugin não são montadas")
    adapter = KanbanAdapter(tmp_path / "kanban.db")
    manager = BotSpecManager(EventStore())
    manager.register(BotSpec(id="worker", name="Worker", routines={"fix": {}}))
    monkeypatch.setattr(plugin_api, "resolve_bot_manager", lambda: manager)
    monkeypatch.setattr(plugin_api, "resolve_kanban_adapter", lambda stats=None: adapter)
    yield adapter
    adapter.close()


@pytest.mark.parametrize("endpoint", ["trigger_bot", "submit_bot"])
def test_bot_submission_runs_in_yolo(endpoint, wired):
    result = getattr(plugin_api, endpoint)("worker", {"routine": "fix", "goal": "Repair it"})

    assert wired.get_task(result["task_id"])["spec"]["yolo_mode"] is True


@pytest.mark.parametrize("endpoint", ["trigger_bot", "submit_bot"])
def test_body_yolo_override_neither_disables_the_policy_nor_crashes(endpoint, wired):
    """A política do operador vence o body — e o body não pode estourar a rota.

    Expandir ``**overrides`` junto de um ``yolo_mode=`` explícito seria TypeError
    quando o body já traz a chave; é o merge que mantém a rota viva.
    """
    result = getattr(plugin_api, endpoint)(
        "worker",
        {"routine": "fix", "goal": "Repair it", "overrides": {"yolo_mode": False}},
    )

    assert wired.get_task(result["task_id"])["spec"]["yolo_mode"] is True
