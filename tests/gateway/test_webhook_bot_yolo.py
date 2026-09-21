"""Webhook assinado -> rotina de bot: a missão nasce em YOLO.

``_handle_bot_trigger`` é a superfície de despacho cujo gatilho é externo
(máquina), não o operador digitando no console. Ainda assim ela nasce em YOLO
(``OPERATOR_YOLO_DEFAULT``) porque o worker é headless: sem isso a missão para
num prompt de aprovação que ninguém está vendo. A fronteira de confiança da rota
continua sendo a assinatura HMAC, verificada antes de chegar aqui.

Exercita o handler REAL com ``BotSpecManager`` e ``KanbanAdapter`` reais; o que se
assere é o spec persistido no kanban canônico.
"""

import json

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.webhook import WebhookAdapter
from hermes.platform.bots import BotSpec, BotSpecManager
from hermes.platform.observability.event_store import EventStore
from hermes.platform.tasks.kanban_adapter import KanbanAdapter


@pytest.fixture
def wired(tmp_path, monkeypatch):
    store = EventStore()
    BotSpecManager(store).register(BotSpec(id="worker", name="Worker", routines={"fix": {}}))
    # `_handle_bot_trigger` faz `from ...event_store import get_event_store` dentro
    # da função, então patchar o módulo de origem é o seam real do processo.
    import hermes.platform.observability.event_store as event_store_module

    monkeypatch.setattr(event_store_module, "get_event_store", lambda: store)

    kanban = KanbanAdapter(tmp_path / "kanban.db")
    config = PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0, "routes": {}})
    adapter = WebhookAdapter(config)
    adapter.gateway_runner = type("Runner", (), {"kanban_adapter": kanban})()
    yield adapter, kanban
    kanban.close()


@pytest.mark.asyncio
async def test_webhook_bot_trigger_dispatches_a_yolo_mission(wired):
    adapter, kanban = wired

    response = await adapter._handle_bot_trigger(
        prompt="Repair it",
        payload={"ref": "main"},
        route_config={"bot_id": "worker", "routine": "fix"},
        route_name="r1",
        event_type="push",
        delivery_id="d1",
    )

    assert response.status == 202
    task_id = json.loads(response.text)["task_id"]
    assert kanban.get_task(task_id)["spec"]["yolo_mode"] is True
