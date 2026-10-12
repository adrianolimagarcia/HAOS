"""Tests for Civilization Reverse bot (first custom citizen) and RE delegation."""
from pathlib import Path

from hermes.platform.bots.builtin.reverse_bot import (
    REVERSE_BOT_ID,
    REVERSE_BOT_NAME,
    REVERSE_BOT_DOMAIN,
    REVERSE_CAPABILITIES,
    get_reverse_bot_spec,
    get_reverse_bot_identity_bundle,
    register_reverse_bot,
)
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.civilization.delegation import route_task, _DOMAINS
from hermes.platform.observability.event_store import EventStore


def _repo_root() -> Path:
    # tests/platform/civilization/test_re_specialist.py -> parents[3] is repo root
    return Path(__file__).resolve().parents[3]


def test_reverse_bot_spec_and_identity_contract():
    spec = get_reverse_bot_spec()
    assert spec.id == "reverse-bot"
    assert spec.name == "Reverse"
    assert "binary-triage" in spec.capabilities
    assert "re-router" in spec.capabilities
    assert "binary-intake" in spec.routines
    assert spec.task_defaults.get("task_class") == "reverse-engineering"
    assert spec.policy.workspace == "git_worktree"
    assert spec.policy.max_runtime_minutes >= 1

    bundle = get_reverse_bot_identity_bundle()
    assert bundle.bot_id == "reverse-bot"
    assert "Civilization" in bundle.metadata.get("subsystem", "")
    assert "reverse-engineering" == bundle.metadata.get("domain")
    assert "dual-use" in bundle.soul.lower()
    assert bundle.bundle_hash != ""


def test_register_reverse_bot_lifecycle():
    store = EventStore()
    spec = register_reverse_bot(store)
    assert spec.id == "reverse-bot"

    # Re-reading via managers
    spec_mgr = BotSpecManager(store)
    id_mgr = IdentityManager(store)
    registered_spec = spec_mgr.get("reverse-bot")
    assert registered_spec is not None
    assert registered_spec.name == "Reverse"

    active_version = id_mgr.get_active_version("reverse-bot")
    assert active_version is not None
    assert active_version.version == 1
    assert active_version.bot_id == "reverse-bot"

    # Idempotent registration
    spec2 = register_reverse_bot(store)
    assert spec2.id == "reverse-bot"


def test_civilization_delegation_routes_to_reverse_bot():
    store = EventStore()
    register_reverse_bot(store)

    # The reverse-bot domain slot pre-exists in _DOMAINS (no delegation.py change needed)
    assert "reverse-bot" in _DOMAINS
    assert "ghidra" in _DOMAINS["reverse-bot"]
    assert "radare2" in _DOMAINS["reverse-bot"]
    assert "binary" in _DOMAINS["reverse-bot"]

    # Capability match routing: RE-flavoured goal lands on reverse-bot
    task = route_task(
        {"goal": "Decompile this ELF binary with Ghidra and list its disassembled functions"},
        event_store=store,
    )
    assert task["bot_id"] == "reverse-bot"
    assert task["identity_version"] == 1

    # Explicit bot_id routing
    task_explicit = route_task(
        {"bot_id": "reverse-bot", "goal": "Triage suspicious binary"},
        event_store=store,
    )
    assert task_explicit["bot_id"] == "reverse-bot"


def test_reverse_bot_capabilities_match_existing_skills():
    """Every declared capability must correspond to a real skill on disk."""
    root = _repo_root()
    for cap in REVERSE_CAPABILITIES:
        skill_md = root / "skills" / "reverse-engineering" / cap / "SKILL.md"
        assert skill_md.is_file(), f"missing SKILL.md for capability {cap}"
        frontmatter = skill_md.read_text(encoding="utf-8").split("---")[1]
        names = [ln.split(":", 1)[1].strip() for ln in frontmatter.splitlines() if ln.startswith("name:")]
        assert cap in names, f"SKILL.md name mismatch for capability {cap}"
