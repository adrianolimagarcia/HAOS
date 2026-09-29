"""``haos civ`` command implementation — Operator interface for HAOS Civilization."""

from __future__ import annotations

import json
from typing import Any, Dict

from hermes_constants import get_hermes_home
from hermes.platform.observability.event_store import EventStore
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.council.manager import CouncilManager
from hermes.platform.society.manager import SocietyManager
from hermes.platform.evolution.bot_evolution import BotEvolutionManager
from hermes.platform.civilization.manager import CivilizationManager


def _get_managers():
    event_db = get_hermes_home() / "events.db"
    store = EventStore(event_db)
    bot_spec_mgr = BotSpecManager(store)
    identity_mgr = IdentityManager(store)
    council_mgr = CouncilManager(store)
    society_mgr = SocietyManager(store)
    evolution_mgr = BotEvolutionManager(store)
    civ_mgr = CivilizationManager(store)
    return {
        "store": store,
        "bots": bot_spec_mgr,
        "identity": identity_mgr,
        "council": council_mgr,
        "society": society_mgr,
        "evolution": evolution_mgr,
        "civ": civ_mgr,
    }


def cmd_civ(args) -> int:
    """Entry point for ``haos civ`` commands."""
    action = getattr(args, "civ_action", "status") or "status"
    as_json = getattr(args, "json", False)

    mgrs = _get_managers()

    if action == "status":
        return _action_status(mgrs, as_json)
    elif action == "bot-create":
        from hermes.platform.civilization.delegation import create_bot
        try:
            bot = create_bot(getattr(args, "bot_id"), getattr(args, "domain"),
                             description=getattr(args, "description", ""),
                             soul=getattr(args, "soul", ""), values=getattr(args, "values", ""),
                             event_store=mgrs["store"])
        except ValueError as exc:
            print(f"Bot registration failed: {exc}")
            return 1
        print(f"Registered Civilization bot: {bot}")
        return 0
    elif action == "bots":
        return _action_bots(mgrs, args, as_json)
    elif action == "council":
        return _action_council(mgrs, args, as_json)
    elif action == "society":
        return _action_society(mgrs, args, as_json)
    elif action == "evolution":
        return _action_evolution(mgrs, args, as_json)
    elif action == "evolution-promote":
        from hermes.platform.civilization.delegation import deliberate_and_promote_proposal
        try:
            res = deliberate_and_promote_proposal(
                getattr(args, "proposal_id"),
                council_id=getattr(args, "council_id", None),
                decision_summary=getattr(args, "decision_summary", ""),
                event_store=mgrs["store"],
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Promoted proposal {res['proposal_id']} -> Bot {res['bot_id']} new version {res['new_version_number']} ({res['new_version_id']})")
            return 0
        except Exception as exc:
            print(f"Failed to promote evolution proposal: {exc}")
            return 1
    elif action == "evolution-rollback":
        from hermes.platform.civilization.delegation import rollback_bot_identity
        try:
            res = rollback_bot_identity(
                getattr(args, "bot_id"),
                getattr(args, "version_id"),
                reason=getattr(args, "reason", "Operator rollback"),
                event_store=mgrs["store"],
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Rolled back Bot {res['bot_id']} to {res['restored_from']} -> Compensatory version {res['version_number']} ({res['new_compensatory_version_id']})")
            return 0
        except Exception as exc:
            print(f"Failed to rollback bot identity: {exc}")
            return 1
    elif action == "reconcile":
        from hermes.platform.civilization.delegation import reconcile_civilization_state
        try:
            res = reconcile_civilization_state(
                event_store=mgrs["store"],
                max_age_seconds=getattr(args, "max_age", 3600.0),
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Reconciled orphaned leaves: {len(res['reconciled_orphaned_leaves'])}")
                print(f"Active bots verified: {res['total_active_bots']}")
                for bid, ver in res["bot_versions"].items():
                    print(f"  - {bid}: version {ver}")
            return 0
        except Exception as exc:
            print(f"Reconciliation failed: {exc}")
            return 1
    elif action == "constitution":
        return _action_constitution(mgrs, as_json)
    elif action == "memory":
        return _action_memory(mgrs, args, as_json)
    elif action == "council-deliberate":
        return _action_council_deliberate(mgrs, args, as_json)
    elif action == "council-debate-turns":
        return _action_council_debate_turns(mgrs, args, as_json)
    elif action == "council-memory-project":
        return _action_council_memory_project(mgrs, args, as_json)
    elif action == "curator-run":
        return _action_curator_run(mgrs, args, as_json)
    elif action == "graph":
        return _action_graph(mgrs, as_json)
    else:
        print(f"Unknown action: {action}")
        return 1


def _action_status(mgrs: Dict[str, Any], as_json: bool) -> int:
    bots = mgrs["bots"].list()
    councils = mgrs["council"].list()
    active_constitution = mgrs["civ"].get_active_constitution()
    reputations = mgrs["society"].get_all_reputations()
    proposals = mgrs["evolution"].get_proposals()
    memory = mgrs["civ"].query_knowledge()

    data = {
        "status": "healthy",
        "counts": {
            "bots": len(bots),
            "councils": len(councils),
            "active_constitution_version": active_constitution.version if active_constitution else None,
            "reputation_records": len(reputations),
            "evolution_proposals": len(proposals),
            "memory_assertions": len(memory),
        },
        "constitution": active_constitution.title if active_constitution else "None enacted",
    }

    if as_json:
        print(json.dumps(data, indent=2))
        return 0

    print("=== HAOS Civilization Status ===")
    print(f"Status:                    {data['status'].upper()}")
    print(f"Registered Bots:           {data['counts']['bots']}")
    print(f"Active Councils:           {data['counts']['councils']}")
    print(f"Constitution:              {data['constitution']} (v{data['counts']['active_constitution_version']})")
    print(f"Reputation Vectors:        {data['counts']['reputation_records']}")
    print(f"Evolution Proposals:       {data['counts']['evolution_proposals']}")
    print(f"Shared Knowledge Items:    {data['counts']['memory_assertions']}")
    return 0


def _action_bots(mgrs: Dict[str, Any], args, as_json: bool) -> int:
    bot_id = getattr(args, "bot_id", None)
    bots = mgrs["bots"].list()
    if bot_id:
        bots = [b for b in bots if getattr(b, "id", None) == bot_id or getattr(b, "bot_id", None) == bot_id]

    records = []
    for b in bots:
        bid = getattr(b, "id", None) or getattr(b, "bot_id", None)
        active_ver = mgrs["identity"].get_active_version(bid)
        records.append({
            "bot_id": bid,
            "name": b.name,
            "status": "active" if active_ver else "uninitialized",
            "version": active_ver.version if active_ver else b.version,
            "bundle_hash": active_ver.bundle_hash if active_ver else "none",
        })

    if as_json:
        print(json.dumps({"bots": records}, indent=2))
        return 0

    if not records:
        print("No bots registered.")
        return 0

    print(f"{'BOT ID':<20} {'NAME':<24} {'STATUS':<15} {'VER':<5} {'BUNDLE HASH':<16}")
    print("-" * 82)
    for r in records:
        print(f"{r['bot_id']:<20} {r['name']:<24} {r['status']:<15} {r['version']:<5} {r['bundle_hash'][:14]:<16}")
    return 0


def _action_council(mgrs: Dict[str, Any], args, as_json: bool) -> int:
    council_id = getattr(args, "council_id", None)
    councils = mgrs["council"].list()
    if council_id:
        councils = [c for c in councils if getattr(c, "id", None) == council_id or getattr(c, "council_id", None) == council_id]

    records = []
    for c in councils:
        cid = getattr(c, "id", None) or getattr(c, "council_id", None)
        purpose = getattr(c, "purpose", None) or getattr(c, "name", "")
        records.append({
            "council_id": cid,
            "name": purpose,
            "members": c.members,
            "decision_mode": getattr(c, "decision_mode", "consensus_with_dissent"),
        })

    if as_json:
        print(json.dumps({"councils": records}, indent=2))
        return 0

    if not records:
        print("No councils registered.")
        return 0

    print(f"{'COUNCIL ID':<20} {'NAME / PURPOSE':<35} {'MEMBERS':<20} {'MODE'}")
    print("-" * 88)
    for r in records:
        members_str = ",".join(r["members"][:3]) + ("..." if len(r["members"]) > 3 else "")
        purpose_clip = r['name'][:33] + (".." if len(r['name']) > 33 else "")
        print(f"{r['council_id']:<20} {purpose_clip:<35} {members_str:<20} {r['decision_mode']}")
    return 0


def _action_society(mgrs: Dict[str, Any], args, as_json: bool) -> int:
    domain = getattr(args, "domain", None)
    reputations = mgrs["society"].get_all_reputations()

    records = []
    for bot_id, rep in reputations.items():
        for d, score in rep.domains.items():
            if domain and d != domain:
                continue
            records.append({
                "bot_id": bot_id,
                "domain": d,
                "score": round(score.score, 3),
                "confidence": round(score.confidence, 3),
                "evidence_count": score.evidence_count,
            })

    records.sort(key=lambda x: x["score"], reverse=True)

    if as_json:
        print(json.dumps({"reputation": records}, indent=2))
        return 0

    if not records:
        print("No reputation records found.")
        return 0

    print(f"{'BOT ID':<20} {'DOMAIN':<16} {'SCORE':<8} {'CONFIDENCE':<12} {'EVIDENCE':<8}")
    print("-" * 66)
    for r in records:
        print(f"{r['bot_id']:<20} {r['domain']:<16} {r['score']:<8} {r['confidence']:<12} {r['evidence_count']:<8}")
    return 0


def _action_evolution(mgrs: Dict[str, Any], args, as_json: bool) -> int:
    bot_id = getattr(args, "bot_id", None)
    proposals = mgrs["evolution"].get_proposals(bot_id=bot_id)

    records = [p.to_dict() for p in proposals]

    if as_json:
        print(json.dumps({"proposals": records}, indent=2))
        return 0

    if not records:
        print("No evolution proposals recorded.")
        return 0

    print(f"{'PROPOSAL ID':<18} {'BOT ID':<16} {'RISK':<12} {'STATUS':<12} {'RATIONALE'}")
    print("-" * 75)
    for r in records:
        rationale_clip = r['rationale'][:30] + ("..." if len(r['rationale']) > 30 else "")
        print(f"{r['id']:<18} {r['bot_id']:<16} {r['risk_class']:<12} {r['status']:<12} {rationale_clip}")
    return 0


def _action_constitution(mgrs: Dict[str, Any], as_json: bool) -> int:
    cv = mgrs["civ"].get_active_constitution()
    if not cv:
        if as_json:
            print(json.dumps({"constitution": None}, indent=2))
        else:
            print("No active constitution enacted.")
        return 0

    if as_json:
        print(json.dumps({"constitution": cv.to_dict()}, indent=2))
        return 0

    print(f"=== Constitution v{cv.version}: {cv.title} ===")
    print(f"Approved By: {cv.approved_by}")
    print("Rules:")
    for r in cv.rules:
        print(f"  - [{r.rule_type.upper()}] {r.name} (action: '{r.target_action}')")
        print(f"    {r.description}")
    return 0


def _action_memory(mgrs: Dict[str, Any], args, as_json: bool) -> int:
    subject = getattr(args, "subject", None)
    predicate = getattr(args, "predicate", None)
    assertions = mgrs["civ"].query_knowledge(subject=subject, predicate=predicate)

    records = [a.to_dict() for a in assertions]

    if as_json:
        print(json.dumps({"assertions": records}, indent=2))
        return 0

    if not records:
        print("No civilization assertions found.")
        return 0

    print(f"{'SUBJECT':<20} {'PREDICATE':<18} {'OBJECT':<20} {'CONFIDENCE'}")
    print("-" * 70)
    for r in records:
        print(f"{r['subject']:<20} {r['predicate']:<18} {r['object']:<20} {r['confidence']}")
    return 0


def _action_council_deliberate(mgrs: Dict[str, Any], args, as_json: bool) -> int:
    from hermes.platform.council.debate_runner import CouncilDebateRunner
    from hermes.platform.council.member_runner import MemberRunner

    council_id = getattr(args, "council_id")
    objective = getattr(args, "objective")
    max_rounds = getattr(args, "max_rounds", 3)
    timeout = getattr(args, "timeout", 30.0)
    command_id = getattr(args, "command_id", None)

    member_runner = MemberRunner(identity_provider=mgrs["identity"])
    runner = CouncilDebateRunner(
        council_manager=mgrs["council"],
        member_runner=member_runner,
        civ_manager=mgrs["civ"],
    )

    try:
        res = runner.deliberate(
            council_id=council_id,
            objective=objective,
            command_id=command_id,
            options={"max_rounds": max_rounds, "timeout_seconds": timeout},
        )
        if as_json:
            print(json.dumps(res, indent=2))
        else:
            print(f"Council Deliberation Completed: session {res.get('session_id')}")
            print(f"Status:   {res.get('status')}")
            print(f"Decision: {res.get('decision')}")
            print(f"Turns:    {len(res.get('debate_turns', []))}")
            if res.get("actions_scheduled"):
                print(f"Scheduled Actions: {res.get('actions_scheduled')}")
        return 0
    except Exception as exc:
        print(f"Deliberation failed: {exc}")
        return 1


def _action_council_debate_turns(mgrs: Dict[str, Any], args, as_json: bool) -> int:
    session_id = getattr(args, "session_id")
    sess = mgrs["council"].get_session(session_id)
    if not sess:
        print(f"Session not found: {session_id}")
        return 1

    turns = [t.to_dict() if hasattr(t, "to_dict") else t for t in sess.debate_turns]
    if as_json:
        print(json.dumps({"session_id": session_id, "debate_turns": turns}, indent=2))
        return 0

    if not turns:
        print(f"No debate turns recorded for session {session_id}.")
        return 0

    print(f"=== Debate Turns for Session {session_id} ===")
    for t in turns:
        bot = t.get("bot_id", "unknown")
        rnd = t.get("round_number", 1)
        phase = t.get("phase", "")
        crit = f" (critique of {t['critique_of']})" if t.get("critique_of") else ""
        print(f"[{bot} | Round {rnd} | {phase}]{crit}")
        print(f"  Position: {t.get('position')}")
        if t.get("evidence_refs"):
            print(f"  Evidence: {', '.join(t.get('evidence_refs'))}")
    return 0


def _action_council_memory_project(mgrs: Dict[str, Any], args, as_json: bool) -> int:
    from hermes.platform.council.memory_projection import CouncilMemoryProjectionEngine

    engine = CouncilMemoryProjectionEngine(mgrs["store"])
    council_id = getattr(args, "council_id", None)
    rebuild = getattr(args, "rebuild", False)

    if rebuild:
        res = engine.rebuild_all()
    elif council_id:
        path = engine.project_council(council_id)
        res = {council_id: str(path)}
    else:
        res = engine.project_all()

    if as_json:
        print(json.dumps({k: str(v) for k, v in res.items()}, indent=2))
    else:
        print(f"Projected {len(res)} Council MEMORY.md files:")
        for cid, path in res.items():
            print(f"  - {cid}: {path}")
    return 0


def _action_curator_run(mgrs: Dict[str, Any], args, as_json: bool) -> int:
    from hermes.platform.evolution.bot_curator import EvolutionCurator

    dry_run = getattr(args, "dry_run", False)

    curator = EvolutionCurator(
        event_store=mgrs["store"],
        id_mgr=mgrs["identity"],
        evo_mgr=mgrs["evolution"],
        council_mgr=mgrs["council"],
    )
    res = curator.run_cycle(dry_run=dry_run)

    if as_json:
        print(json.dumps(res, indent=2))
    else:
        print(f"Curator run completed (dry_run={dry_run}):")
        print(f"  Status:               {res.get('status')}")
        print(f"  Experiences ingested: {res.get('experiences_ingested', 0)}")
        print(f"  Proposals generated:  {res.get('proposals_generated', 0)}")
        print(f"  Cursor Seq:           {res.get('cursor_seq', 0)}")
        for pid in res.get("created_proposal_ids", []):
            print(f"    * Proposal ID: {pid}")
    return 0


def _action_graph(mgrs: Dict[str, Any], as_json: bool) -> int:
    from hermes.platform.civilization.graph import build_civ_graph

    graph = build_civ_graph(mgrs["store"])
    if as_json:
        print(json.dumps(graph, indent=2))
    else:
        print("=== Civilization Graph DAG ===")
        print(
            f"Nodes: {graph['counts']['nodes']} (bots: {graph['counts']['bots']}, councils: {graph['counts']['councils']}, decisions: {graph['counts']['decisions']}, proposals: {graph['counts']['proposals']})"
        )
        print(f"Edges: {graph['counts']['edges']} (relationships: {graph['counts']['relationships']})")
        print(f"Event Cursor: {graph['cursor']}")
    return 0

