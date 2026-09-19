from pathlib import Path

import pytest

from hermes.platform.webui.agent_hierarchy import AgentHierarchyStore, HierarchyError


def test_hierarchy_supports_master_managers_bots_and_advisory_turn_limit(tmp_path):
    store = AgentHierarchyStore(tmp_path / "hierarchy.json")
    master = store.upsert_node({"name": "Mestre", "role": "master", "model": "m"})
    left = store.upsert_node({"name": "Gerente A", "role": "manager", "parent_id": master["id"], "model": "a"})
    right = store.upsert_node({"name": "Gerente B", "role": "manager", "parent_id": master["id"], "model": "b"})
    bot = store.upsert_node({"name": "Bot", "role": "bot", "parent_id": left["id"]})
    store.set_bot_model("provider", "shared-bot")
    edge = store.set_advisory_edge(left["id"], right["id"], 4)
    snapshot = store.snapshot()
    assert {n["id"] for n in snapshot["nodes"]} == {master["id"], left["id"], right["id"], bot["id"]}
    assert snapshot["bot_model"]["model"] == "shared-bot"
    assert edge["max_turns"] == 4


def test_council_consumes_and_enforces_turn_budget(tmp_path):
    store = AgentHierarchyStore(tmp_path / "hierarchy.json")
    master = store.upsert_node({"name": "Mestre", "role": "master"})
    a = store.upsert_node({"name": "A", "role": "manager", "parent_id": master["id"]})
    b = store.upsert_node({"name": "B", "role": "manager", "parent_id": master["id"]})
    store.set_advisory_edge(a["id"], b["id"], 2)
    council = store.start_council(a["id"], b["id"], "decidir", 2)
    store.append_council_turn(council["id"], a["id"], "proposta")
    final = store.append_council_turn(council["id"], b["id"], "parecer")
    assert final["status"] == "exhausted"
    with pytest.raises(HierarchyError, match="exhausted"):
        store.append_council_turn(council["id"], a["id"], "mais")


def test_hierarchy_rejects_invalid_parent_roles(tmp_path):
    store = AgentHierarchyStore(tmp_path / "hierarchy.json")
    master = store.upsert_node({"name": "Mestre", "role": "master"})
    with pytest.raises(HierarchyError, match="bot must report"):
        store.upsert_node({"name": "Bot", "role": "bot", "parent_id": master["id"]})
