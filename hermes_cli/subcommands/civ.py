"""``haos civ`` subcommand parser — HAOS Civilization Control Plane."""

from __future__ import annotations


def build_civ_parser(subparsers) -> None:
    """Attach the ``civ`` subcommand to ``subparsers``."""
    civ_parser = subparsers.add_parser(
        "civ",
        help="Inspect and manage HAOS Civilization (Bots, Council, Society, Evolution, Constitution)",
        description="Operator control plane for HAOS Agent Civilization architecture.",
    )
    civ_subparsers = civ_parser.add_subparsers(dest="civ_action")

    # haos civ status
    civ_status = civ_subparsers.add_parser(
        "status",
        help="Show overall civilization health, active constitution, active council, and bot counts",
    )
    civ_status.add_argument("--json", action="store_true", help="Output as JSON")

    # haos civ bot-create
    civ_create = civ_subparsers.add_parser("bot-create", help="Create and register a Civilization specialist bot")
    civ_create.add_argument("--bot-id", required=True, help="Stable bot identifier")
    civ_create.add_argument("--domain", required=True, help="Specialization domain")
    civ_create.add_argument("--description", default="", help="Bot mission description")
    civ_create.add_argument("--soul", default="", help="Optional SOUL text")
    civ_create.add_argument("--values", default="", help="Optional VALUES text")

    # haos civ bots
    civ_bots = civ_subparsers.add_parser("bots", help="List registered bots and identity versions")
    civ_bots.add_argument("--json", action="store_true", help="Output as JSON")
    civ_bots.add_argument("--bot-id", help="Filter by specific bot ID")

    # haos civ council
    civ_council = civ_subparsers.add_parser("council", help="Inspect councils, sessions, and decisions")
    civ_council.add_argument("--json", action="store_true", help="Output as JSON")
    civ_council.add_argument("--council-id", help="Filter by council ID")

    # haos civ society
    civ_society = civ_subparsers.add_parser("society", help="Inspect bot relationships and reputation vectors")
    civ_society.add_argument("--json", action="store_true", help="Output as JSON")
    civ_society.add_argument("--domain", help="Filter reputation by domain")

    # haos civ evolution
    civ_evolution = civ_subparsers.add_parser("evolution", help="Inspect evolution proposals and experiences")
    civ_evolution.add_argument("--json", action="store_true", help="Output as JSON")
    civ_evolution.add_argument("--bot-id", help="Filter proposals by bot ID")

    # haos civ evolution-promote
    civ_evo_prom = civ_subparsers.add_parser("evolution-promote", help="Deliberate and promote an evolution proposal")
    civ_evo_prom.add_argument("--proposal-id", required=True, help="Proposal ID to promote")
    civ_evo_prom.add_argument("--council-id", help="Optional Council ID to record deliberation")
    civ_evo_prom.add_argument("--decision-summary", default="", help="Council decision summary")
    civ_evo_prom.add_argument("--json", action="store_true", help="Output as JSON")

    # haos civ evolution-rollback
    civ_evo_roll = civ_subparsers.add_parser("evolution-rollback", help="Roll back bot identity to previous version")
    civ_evo_roll.add_argument("--bot-id", required=True, help="Target bot ID")
    civ_evo_roll.add_argument("--version-id", required=True, help="Target version ID to restore")
    civ_evo_roll.add_argument("--reason", default="Operator rollback", help="Rollback justification")
    civ_evo_roll.add_argument("--json", action="store_true", help="Output as JSON")

    # haos civ reconcile
    civ_rec = civ_subparsers.add_parser("reconcile", help="Scan and reconcile orphaned leaves and verify state consistency")
    civ_rec.add_argument("--max-age", type=float, default=3600.0, help="Max active age before leaf is marked orphaned")
    civ_rec.add_argument("--json", action="store_true", help="Output as JSON")

    # haos civ constitution
    civ_const = civ_subparsers.add_parser("constitution", help="View active constitution rules and policy status")
    civ_const.add_argument("--json", action="store_true", help="Output as JSON")

    # haos civ memory
    civ_mem = civ_subparsers.add_parser("memory", help="Query shared civilization knowledge assertions")
    civ_mem.add_argument("--json", action="store_true", help="Output as JSON")
    civ_mem.add_argument("--subject", help="Filter assertions by subject")
    civ_mem.add_argument("--predicate", help="Filter assertions by predicate")

    # haos civ council-deliberate
    civ_delib = civ_subparsers.add_parser("council-deliberate", help="Trigger autonomous council deliberation session")
    civ_delib.add_argument("--council-id", required=True, help="Target council identifier")
    civ_delib.add_argument("--objective", required=True, help="Deliberation objective or proposal")
    civ_delib.add_argument("--max-rounds", type=int, default=3, help="Max debate rounds")
    civ_delib.add_argument("--timeout", type=float, default=30.0, help="Session timeout in seconds")
    civ_delib.add_argument("--command-id", help="Optional idempotency command ID")
    civ_delib.add_argument("--json", action="store_true", help="Output as JSON")

    # haos civ council-debate-turns
    civ_turns = civ_subparsers.add_parser("council-debate-turns", help="Inspect all debate turns recorded in a council session")
    civ_turns.add_argument("--session-id", required=True, help="Council session ID")
    civ_turns.add_argument("--json", action="store_true", help="Output as JSON")

    # haos civ council-memory-project
    civ_proj = civ_subparsers.add_parser("council-memory-project", help="Project deterministic MEMORY.md files for councils")
    civ_proj.add_argument("--council-id", help="Optional specific council ID")
    civ_proj.add_argument("--rebuild", action="store_true", help="Rebuild all memory files from scratch")
    civ_proj.add_argument("--json", action="store_true", help="Output as JSON")

    # haos civ curator-run
    civ_cur = civ_subparsers.add_parser("curator-run", help="Run background EvolutionCurator pass")
    civ_cur.add_argument("--bot-id", help="Optional target bot ID")
    civ_cur.add_argument("--batch-size", type=int, default=50, help="Max experience events per bot")
    civ_cur.add_argument("--dry-run", action="store_true", help="Simulate curation without persisting proposals")
    civ_cur.add_argument("--json", action="store_true", help="Output as JSON")

    # haos civ graph
    civ_graph = civ_subparsers.add_parser("graph", help="Project civilization graph DAG (nodes and edges)")
    civ_graph.add_argument("--json", action="store_true", help="Output as JSON")

    def _dispatch_civ(args):
        from hermes_cli.civ_cmd import cmd_civ

        if not getattr(args, "civ_action", None):
            args.civ_action = "status"
        return cmd_civ(args)

    civ_parser.set_defaults(func=_dispatch_civ)
