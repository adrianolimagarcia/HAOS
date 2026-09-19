from unittest.mock import Mock

from hermes.platform.models.client import CompletionResult
from hermes.platform.models.model_resolver import ModelResolver
from hermes.platform.models.profiles import ModelIdentity, ModelProfile, ProviderRoute
from hermes.platform.observability.event_store import EventStore
from hermes.platform.webui.agent_hierarchy import AgentHierarchyStore
from hermes.platform.webui.council_adapter import CouncilAdapter


def test_council_adapter_calls_model_and_consumes_turn(tmp_path):
    store = AgentHierarchyStore(tmp_path / "hierarchy.json")
    events = EventStore(str(tmp_path / "events.db"))
    master = store.upsert_node({"name": "M", "role": "master"})
    a = store.upsert_node({"name": "A", "role": "manager", "parent_id": master["id"], "model": "mgr"})
    b = store.upsert_node({"name": "B", "role": "manager", "parent_id": master["id"], "model": "mgr"})
    store.set_advisory_edge(a["id"], b["id"], 2)
    council = store.start_council(a["id"], b["id"], "decide", 2)
    profile = ModelProfile("mgr", ModelIdentity("family", "variant"), [ProviderRoute("mock", "mock-model")])
    resolver = Mock(); resolver.resolve.return_value = profile
    client = Mock(); client.complete.return_value = CompletionResult("answer", "family", "variant", "mock", "mock-model")
    result = CouncilAdapter(store, events, client=client, resolver=resolver).run_turn(council["id"], a["id"], "advise")
    assert result["content"] == "answer"
    assert result["council"]["turns_used"] == 1
    client.complete.assert_called_once()
