"""Tests for Civilization Lean bot (second custom citizen) and lean delegation."""
from pathlib import Path

from hermes.platform.bots.builtin.lean_bot import (
    LEAN_BOT_ID,
    LEAN_BOT_NAME,
    LEAN_BOT_DOMAIN,
    LEAN_CAPABILITIES,
    get_lean_bot_spec,
    get_lean_bot_identity_bundle,
    register_lean_bot,
    run_overengineering_scan,
)
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.civilization.delegation import route_task, _DOMAINS
from hermes.platform.observability.event_store import EventStore


def _repo_root() -> Path:
    # tests/platform/civilization/test_lean_specialist.py -> parents[3] is repo root
    return Path(__file__).resolve().parents[3]


def test_lean_bot_spec_and_identity_contract():
    spec = get_lean_bot_spec()
    assert spec.id == "lean-bot"
    assert spec.name == "Lean"
    assert "ponytail" in spec.capabilities
    assert "ponytail-review" in spec.capabilities
    assert "simplicity-audit" in spec.routines
    assert spec.task_defaults.get("task_class") == "code-simplicity"
    assert spec.policy.workspace == "git_worktree"
    assert spec.policy.max_runtime_minutes >= 1

    bundle = get_lean_bot_identity_bundle()
    assert bundle.bot_id == "lean-bot"
    assert "Civilization" in bundle.metadata.get("subsystem", "")
    assert "anti-over-engineering" == bundle.metadata.get("domain")
    assert "yagni" in bundle.soul.lower()
    assert "lazy" in bundle.soul.lower()
    assert "never compromise" in bundle.soul.lower()
    assert bundle.bundle_hash != ""


def test_register_lean_bot_lifecycle():
    store = EventStore()
    spec = register_lean_bot(store)
    assert spec.id == "lean-bot"

    # Re-reading via managers
    spec_mgr = BotSpecManager(store)
    id_mgr = IdentityManager(store)
    registered_spec = spec_mgr.get("lean-bot")
    assert registered_spec is not None
    assert registered_spec.name == "Lean"

    active_version = id_mgr.get_active_version("lean-bot")
    assert active_version is not None
    assert active_version.version == 1
    assert active_version.bot_id == "lean-bot"

    # Idempotent registration
    spec2 = register_lean_bot(store)
    assert spec2.id == "lean-bot"


def test_civilization_delegation_routes_to_lean_bot():
    store = EventStore()
    register_lean_bot(store)

    # The lean-bot domain slot was added to _DOMAINS by this change
    assert "lean-bot" in _DOMAINS
    assert "yagni" in _DOMAINS["lean-bot"]
    assert "ponytail" in _DOMAINS["lean-bot"]
    assert "over-engineering" in _DOMAINS["lean-bot"]

    # Capability match routing: simplicity-flavoured goal lands on lean-bot
    task = route_task(
        {"goal": "Review this PR for over-engineering and simplify the boilerplate using YAGNI"},
        event_store=store,
    )
    assert task["bot_id"] == "lean-bot"
    assert task["identity_version"] == 1

    # Explicit bot_id routing
    task_explicit = route_task(
        {"bot_id": "lean-bot", "goal": "Audit this module for dead code"},
        event_store=store,
    )
    assert task_explicit["bot_id"] == "lean-bot"


def test_lean_bot_capabilities_match_existing_skills():
    """Every declared capability must correspond to a real skill on disk."""
    root = _repo_root()
    for cap in LEAN_CAPABILITIES:
        skill_md = root / "skills" / "development" / cap / "SKILL.md"
        assert skill_md.is_file(), f"missing SKILL.md for capability {cap}"
        frontmatter = skill_md.read_text(encoding="utf-8").split("---")[1]
        names = [ln.split(":", 1)[1].strip() for ln in frontmatter.splitlines() if ln.startswith("name:")]
        assert cap in names, f"SKILL.md name mismatch for capability {cap}"


def test_run_overengineering_scan_degrades_gracefully():
    """Without the lean-agent-cleanroom merge, the scan helper reports cleanly."""
    result = run_overengineering_scan("/tmp")
    assert "ok" in result
    if not result["ok"]:
        # Script not yet merged -> clear message, no exception
        assert "lean-agent-cleanroom" in result["error"]
