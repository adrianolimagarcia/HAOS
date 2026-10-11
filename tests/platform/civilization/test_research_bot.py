"""Tests for Civilization Research bot and temporal research engine integration."""
from hermes.platform.bots.builtin.research_bot import (
    RESEARCH_BOT_ID,
    RESEARCH_BOT_NAME,
    RESEARCH_BOT_DOMAIN,
    RESEARCH_CAPABILITIES,
    get_research_bot_spec,
    get_research_bot_identity_bundle,
    register_research_bot,
    run_bot_research,
)
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.civilization.delegation import route_task, _DOMAINS
from hermes.platform.observability.event_store import EventStore
from hermes.platform.research.temporal_engine import (
    compute_temporal_window,
    execute_temporal_research,
)


def test_research_bot_spec_and_identity_contract():
    spec = get_research_bot_spec()
    assert spec.id == "research-bot"
    assert spec.name == "Research"
    assert "temporal-research" in spec.capabilities
    assert "web-search" in spec.capabilities
    assert "monthly-sweep" in spec.routines
    assert "weekly-sweep" in spec.routines
    assert spec.task_defaults.get("task_class") == "research"
    assert spec.policy.workspace == "git_worktree"

    bundle = get_research_bot_identity_bundle()
    assert bundle.bot_id == "research-bot"
    assert "Civilization" in bundle.metadata.get("subsystem", "")
    assert "temporal-research" == bundle.metadata.get("domain")
    assert bundle.bundle_hash != ""


def test_register_research_bot_lifecycle():
    store = EventStore()
    spec = register_research_bot(store)
    assert spec.id == "research-bot"

    # Re-reading via managers
    spec_mgr = BotSpecManager(store)
    id_mgr = IdentityManager(store)
    registered_spec = spec_mgr.get("research-bot")
    assert registered_spec is not None
    assert registered_spec.name == "Research"

    active_version = id_mgr.get_active_version("research-bot")
    assert active_version is not None
    assert active_version.version == 1
    assert active_version.bot_id == "research-bot"

    # Idempotent registration
    spec2 = register_research_bot(store)
    assert spec2.id == "research-bot"


def test_civilization_delegation_routes_to_research_bot():
    store = EventStore()
    register_research_bot(store)

    assert "research-bot" in _DOMAINS
    assert "last30days" in _DOMAINS["research-bot"]
    assert "temporal" in _DOMAINS["research-bot"]

    # Capability match routing
    task = route_task(
        {"goal": "Investigate AI advancements over the last30days temporal window"},
        event_store=store,
    )
    assert task["bot_id"] == "research-bot"
    assert task["identity_version"] == 1

    # Explicit bot_id routing
    task_explicit = route_task(
        {"bot_id": "research-bot", "goal": "Weekly market analysis"},
        event_store=store,
    )
    assert task_explicit["bot_id"] == "research-bot"


def test_research_bot_consumes_temporal_engine():
    window = compute_temporal_window("last30days")
    assert window.requested == "last30days"
    assert window.start_utc < window.end_utc

    res = run_bot_research("Audit CVE releases", window_spec="last30days")
    assert res.contract_version == 1
    assert res.window is not None
    assert res.window.requested == "last30days"
    assert len(res.coverage) > 0
    assert res.coverage[0].status == "ok"
    assert res.disagreements == []
    assert res.evidence_gaps == []
