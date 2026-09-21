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
    manager = BotSpecManager(store)
    manager.register(BotSpec(id="worker", name="Worker", routines={"fix": {}}))
    # `_handle_bot_trigger` faz `from ...event_store import get_event_store` dentro
    # da função, então patchar o módulo de origem é o seam real do processo.
    import hermes.platform.observability.event_store as event_store_module

    monkeypatch.setattr(event_store_module, "get_event_store", lambda: store)

    kanban = KanbanAdapter(tmp_path / "kanban.db")
    config = PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0, "routes": {}})
    adapter = WebhookAdapter(config)
    adapter.gateway_runner = type("Runner", (), {"kanban_adapter": kanban})()
    # O manager é stateless (o ledger vive no EventStore), então este handle lê
    # exatamente os mesmos eventos que o manager criado dentro do handler gravou.
    yield adapter, kanban, manager
    kanban.close()


@pytest.mark.asyncio
async def test_webhook_bot_trigger_dispatches_a_yolo_mission(wired):
    adapter, kanban, manager = wired

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
    # O evento externo que disparou a missão fica registrado como proveniência
    # no ledger de runs do bot, não só no spec.
    assert manager.run_history("worker", "fix")[-1]["trigger_event"] == "push"


@pytest.mark.asyncio
async def test_webhook_bot_trigger_reaches_persisted_completed_run(wired):
    """Webhook admission reaches terminal state through canonical Kanban APIs."""
    adapter, kanban, manager = wired
    response = await adapter._handle_bot_trigger(
        prompt="Repair it", payload={"ref": "main"},
        route_config={"bot_id": "worker", "routine": "fix"},
        route_name="r1", event_type="push", delivery_id="d-complete",
    )
    assert response.status == 202
    task_id = json.loads(response.text)["task_id"]
    assert kanban.claim_task(task_id, worker_id="e2e-worker")
    assert kanban.complete_task(task_id, summary="repaired")
    manager.record_run("worker", "fix", task_id, "completed")
    assert kanban.get_task(task_id)["status"] == "done"
    replayed = BotSpecManager(manager.event_store)
    assert replayed.run_history("worker", "fix")[-1]["run_id"] == task_id
    assert replayed.run_history("worker", "fix")[-1]["status"] == "completed"
