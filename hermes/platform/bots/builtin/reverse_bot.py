"""Canonical specification and identity for Civilization Reverse bot.

First custom citizen of the HAOS Civilization: a reverse-engineering specialist
bot that consumes the reverse-engineering skill toolchain
(binary-triage, re-router with radare2/Ghidra backends).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.bots.identity import BotIdentityBundle, BotIdentitySpec
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.spec import BotPolicy, BotSpec, TriggerSpec
from hermes.platform.observability.event_store import EventStore

REVERSE_BOT_ID = "reverse-bot"
REVERSE_BOT_NAME = "Reverse"
REVERSE_BOT_DOMAIN = "reverse-engineering"
REVERSE_CAPABILITIES = ["binary-triage", "re-router"]

REVERSE_SOUL = """You are Reverse, a bounded HAOS Civilization specialist bot for reverse engineering and binary analysis.
You operate within the Civilization subsystem and consume the HAOS reverse-engineering skill toolchain (binary-triage, re-router, radare2, Ghidra).
You strictly enforce the HAOS dual-use safety contract:
- Always route sensitive analyses through the RE permission broker before proceeding.
- Never assist in creating malware, offensive exploits, or evasion techniques; defensive analysis (triage, IOC extraction, vulnerability assessment for patching) is permitted.
- Distinguish confirmed findings from hypotheses; cite evidence (offsets, function names, decompiled snippets) for every claim.
- Prefer the cheapest sufficient tier: triage first, radare2 for control flow, Ghidra headless only for deep decompilation.
- Explain findings didactically: what the binary does, how you know, and what remains uncertain.
"""

REVERSE_IDENTITY = """Role: Specialist Civilization bot for reverse engineering, binary triage, and disassembly.
Subsystem: HAOS Civilization
Capabilities: binary-triage, re-router
Consumes: skills/reverse-engineering/binary-triage and skills/reverse-engineering/re-router (radare2/Ghidra backends)
Values: Technical precision, dual-use ethics, evidence before conclusions.
"""

REVERSE_VALUES = """Evidence before conclusions; methodical tiered analysis; dual-use caution - defensive analysis only, never offensive tooling; explicit uncertainty; teach while reporting."""


def _repo_root() -> Path:
    # hermes/platform/bots/builtin/reverse_bot.py -> parents[4] is the repo root
    return Path(__file__).resolve().parents[4]


def get_reverse_bot_spec() -> BotSpec:
    """Return canonical BotSpec for the Reverse bot."""
    return BotSpec(
        id=REVERSE_BOT_ID,
        name=REVERSE_BOT_NAME,
        description="Civilization specialist bot for reverse engineering, binary triage, and disassembly.",
        capabilities=list(REVERSE_CAPABILITIES),
        task_defaults={
            "posture": "analyst",
            "task_class": "reverse-engineering",
            "required_capabilities": list(REVERSE_CAPABILITIES),
        },
        policy=BotPolicy(
            tools=["terminal", "radare2-mcp", "ghidra-mcp"],
            workspace="git_worktree",
            max_cost_usd=10.0,
            max_runtime_minutes=30,
        ),
        routines={
            "binary-intake": {
                "version": 1,
                "task_defaults": {
                    "goal": "Run Tier-1 static triage on a new binary and decide escalation",
                    "priority": 70,
                },
                "prompt": "Triage the target binary with binary-triage; report packers, mitigations, and whether Tier-2/3 analysis is warranted",
            },
        },
        triggers=[
            TriggerSpec(
                id="manual-analysis",
                type="manual",
                name="Manual reverse-engineering trigger",
            )
        ],
        identity=BotIdentitySpec(),
    )


def get_reverse_bot_identity_bundle() -> BotIdentityBundle:
    """Return canonical BotIdentityBundle for the Reverse bot."""
    return BotIdentityBundle(
        bot_id=REVERSE_BOT_ID,
        soul=REVERSE_SOUL,
        identity=REVERSE_IDENTITY,
        values=REVERSE_VALUES,
        metadata={
            "created_by": "civ-builtin",
            "domain": REVERSE_BOT_DOMAIN,
            "subsystem": "Civilization",
        },
    )


def register_reverse_bot(
    event_store: EventStore,
    *,
    activate_identity: bool = True,
) -> BotSpec:
    """Register the Reverse bot into BotSpecManager and IdentityManager if not present."""
    spec_mgr = BotSpecManager(event_store)
    id_mgr = IdentityManager(event_store)

    existing = spec_mgr.get(REVERSE_BOT_ID)
    if existing:
        return existing

    spec = get_reverse_bot_spec()
    spec_mgr.register(spec)

    bundle = get_reverse_bot_identity_bundle()
    id_mgr.create_version(REVERSE_BOT_ID, bundle, activate=activate_identity)
    return spec


def run_bot_triage(binary_path: str) -> Dict[str, Any]:
    """Run Tier-1 static triage (binary-triage skill) on behalf of Reverse bot.

    Invokes skills/reverse-engineering/binary-triage/scripts/binary_analyzer.py
    and returns the parsed JSON report.
    """
    analyzer = (
        _repo_root()
        / "skills"
        / "reverse-engineering"
        / "binary-triage"
        / "scripts"
        / "binary_analyzer.py"
    )
    if not analyzer.is_file():
        raise FileNotFoundError(f"binary-triage analyzer not found: {analyzer}")
    proc = subprocess.run(
        [sys.executable, str(analyzer), "--file", binary_path],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()[-2000:]}
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "triage output was not valid JSON", "raw": proc.stdout[:2000]}
    report["ok"] = True
    return report
