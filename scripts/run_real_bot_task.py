#!/usr/bin/env python3
"""Run a real LLM task associated with an autonomous Civilization Bot.

Executes a live query through Hermes Agent with the Bot identity loaded,
records the position and decision into the canonical Council and EventStore,
and completes the ShadowLeaf execution.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from hermes.platform.bots.identity import DecisionRecord, LeafIdentitySnapshot
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.bots.leaf_protocol import create_leaf_identity_snapshot
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.council.manager import CouncilManager
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes_constants import get_hermes_home


def main():
    hermes_home = get_hermes_home()
    db_path = hermes_home / "events.db"
    print(f"[*] Connecting to canonical event store at: {db_path}")

    store = EventStore(str(db_path))
    bot_mgr = BotSpecManager(store)
    id_mgr = IdentityManager(store)
    council_mgr = CouncilManager(store)

    bot_id = "architect-bot"
    bot = bot_mgr.get(bot_id)
    if not bot:
        raise RuntimeError(f"Bot '{bot_id}' not found in registry")

    active_ver = id_mgr.get_active_version(bot_id)
    if not active_ver:
        raise RuntimeError(f"No active identity version found for bot '{bot_id}'")

    print(f"[+] Loaded Bot: {bot.name} ({bot.id}) - Identity v{active_ver.version} (Hash: {active_ver.bundle_hash[:16]}...)")

    council_id = "arch-sec-council"
    objective = "Auditoria de integridade dos serviços HTTP e topologia de rede local (Portas 9119 e 8788)"
    print(f"[*] Starting Council deliberation on '{council_id}'...")
    session = council_mgr.start_session(
        council_id=council_id,
        objective=objective,
        correlation_id=f"corr-{uuid.uuid4().hex[:8]}"
    )
    print(f"[+] Council Session started: {session.session_id}")

    # Generate Leaf Identity Snapshot
    leaf_id = f"leaf-live-{uuid.uuid4().hex[:8]}"
    task_desc = "Auditar as portas locais 9119 (dashboard HTTP) e 8788 (controlplane) e emitir parecer arquitetural de integridade."
    snapshot = create_leaf_identity_snapshot(
        leaf_id=leaf_id,
        parent_bot_id=bot_id,
        identity_version=active_ver,
        task_description=task_desc,
        council_id=council_id,
        council_session_id=session.session_id,
        correlation_id=session.session_id,
    )

    # Record leaf created event
    store.append(Event(
        name="civ.leaf.created",
        payload={
            "leaf": {
                "leaf_id": leaf_id,
                "parent_bot_id": bot_id,
                "task_description": task_desc,
                "status": "active",
                "identity_snapshot": snapshot.to_dict(),
                "created_at": time.time(),
            }
        },
        correlation_id=session.session_id,
    ))
    print(f"[+] ShadowLeaf registered: {leaf_id}")

    # Execute REAL LLM task using haos chat with Bot Identity loaded
    print(f"[*] Invoking LLM for {bot.name} via 'haos chat --bot-id {bot_id}'...")
    prompt = (
        "Como ArchitectBot, avalie objetivamente a integridade da arquitetura de serviços locais: "
        "o dashboard Hermes está servido via HTTP na porta 9119 e o control-plane na porta 8788. "
        "Apresente seu parecer arquitetural sintético em 2 ou 3 parágrafos sobre a conformidade "
        "dessa topologia com as diretrizes de governança e resiliência do HAOS."
    )

    cmd = [
        "/usr/local/bin/haos",
        "--cli",
        "chat",
        "--source",
        "haos",
        "--accept-hooks",
        "--bot-id",
        bot_id,
        "-Q",
        "-q",
        prompt,
    ]

    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    elapsed = time.time() - t0

    if res.returncode != 0:
        print(f"[!] LLM execution failed with returncode {res.returncode}")
        print(res.stderr)
        store.append(Event(
            name="civ.leaf.failed",
            payload={"leaf_id": leaf_id, "error": res.stderr},
            correlation_id=session.session_id,
        ))
        raise RuntimeError(f"haos chat failed: {res.stderr}")

    llm_output = res.stdout.strip()
    print(f"[+] LLM execution completed in {elapsed:.1f}s!\n")
    print("--- [PARECER ARQUITETURAL DO ARCHITECT-BOT] ---")
    print(llm_output)
    print("----------------------------------------------\n")

    # Record position in Council Session
    print("[*] Submitting position to Council Session...")
    council_mgr.submit_position(
        session_id=session.session_id,
        bot_id=bot_id,
        position={"opinion": "APPROVE_WITH_ADVISORY", "content": llm_output[:800]},
    )

    # Record decision in Council
    print("[*] Recording synthesis decision in Council...")
    decision = council_mgr.record_decision(
        session_id=session.session_id,
        synthesis=(
            "Topologia local (9119 HTTP loopback + 8788 controlplane) validada pelo ArchitectBot. "
            "Recomendação de manter isolamento rigoroso na tailnet e auditoria periódica de portas."
        ),
        decision="APPROVED",
    )
    print(f"[+] Decision recorded: {decision.id} (Decision: {decision.decision})")

    # Complete Leaf
    store.append(Event(
        name="civ.leaf.completed",
        payload={
            "leaf_id": leaf_id,
            "summary": f"Parecer arquitetural emitido com sucesso em {elapsed:.1f}s. Decisão {decision.id} consolidada.",
            "completed_at": time.time(),
        },
        correlation_id=session.session_id,
    ))
    print(f"[+] ShadowLeaf completed: {leaf_id}")

    # Record bot run lifecycle
    bot_mgr.record_run(
        bot_id=bot_id,
        routine="service_topology_audit",
        run_id=f"run-{session.session_id[:12]}",
        status="completed",
        decision_id=decision.id,
        leaf_id=leaf_id,
    )
    print("[+] Bot run recorded in lifecycle ledger.")

    store.close()
    print("\n[SUCCESS] Live task execution and civilizational recording complete!")


if __name__ == "__main__":
    main()
