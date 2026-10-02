"""Parser for bounded HAOS operational cycles and operator-approved saved plans."""
from __future__ import annotations


def build_framework_parser(subparsers):
    from hermes_cli.framework_cmd import cmd_framework

    parser = subparsers.add_parser("framework", help="Inspect, plan and apply bounded HAOS operational actions")
    actions = parser.add_subparsers(dest="framework_action", required=True)
    for action, help_text in (
        ("status", "Read the latest persisted framework state"),
        ("observe", "Collect bounded read-only host telemetry"),
        ("run", "Preview one operational cycle (always dry-run)"),
        ("plan", "Create a saved dry-run plan"),
        ("approve", "Approve one exact saved-plan step with an expiry"),
        ("grant", "Authorize an exact saved-plan action and its rollback in policy"),
        ("apply", "Apply a saved plan through policy and receipt gates"),
    ):
        child = actions.add_parser(action, help=help_text)
        child.add_argument("--base-dir", help="Framework storage root (default: active profile home/agent)")
        child.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
        child.set_defaults(func=cmd_framework)
        if action == "run":
            child.add_argument("--dry-run", action="store_true", default=True, help="Explicitly select preview mode (default)")
        elif action == "plan":
            child.add_argument("--report", action="store_true", help="Plan the fixed reversible operational-report artifact")
        elif action in {"approve", "grant"}:
            child.add_argument("plan_id")
            child.add_argument("--step", required=True, dest="step_id")
            if action == "approve":
                child.add_argument("--ttl", type=float, default=300, dest="ttl_seconds", help="Approval lifetime in seconds, at most 3600")
            else:
                child.add_argument("--autonomous", action="store_true", help="Explicitly grant autonomous execution of this exact intent")
        elif action == "apply":
            child.add_argument("plan_id")
            child.add_argument("--mode", choices=["assisted", "autonomous"], default="assisted")
            child.add_argument("--approval", action="append", default=[], dest="approvals", help="Issued approval ID; repeat per step")
    return parser
