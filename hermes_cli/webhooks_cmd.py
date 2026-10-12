"""CLI commands for managing Webhooks."""

from __future__ import annotations

import argparse
from typing import List, Optional

from hermes.platform.webhooks.models import generate_webhook
from hermes.platform.webhooks.store import get_webhook_store


def register_webhooks_subcommands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("webhooks", help="Manage incoming capability webhooks")
    wh_sub = parser.add_subparsers(dest="webhook_action", required=True)

    # create
    create_p = wh_sub.add_parser("create", help="Create a new webhook")
    create_p.add_argument("--event", "-e", action="append", dest="events", help="Subscribed event type (repeatable)")
    create_p.add_argument("--delivery", "-d", choices=["run", "post"], default="run", help="Delivery mode")
    create_p.add_argument("--rate-limit", "-r", type=int, default=10, help="Rate limit requests/minute")
    create_p.add_argument("--desc", default="", help="Description")

    # list
    wh_sub.add_parser("list", aliases=["ls"], help="List active webhooks")

    # revoke
    revoke_p = wh_sub.add_parser("revoke", help="Revoke a webhook")
    revoke_p.add_argument("webhook_id", help="ID of webhook to revoke")


def handle_webhooks_cli(args: argparse.Namespace) -> int:
    store = get_webhook_store()
    action = getattr(args, "webhook_action", None)

    if action == "create":
        wh, secret = generate_webhook(
            event_types=args.events or [],
            delivery=args.delivery,
            rate_limit=args.rate_limit,
            description=args.desc,
        )
        store.save(wh)
        print(f"Created webhook {wh.id}")
        print(f"  Delivery mode: {wh.delivery}")
        print(f"  Events: {wh.event_types or ['*']}")
        print(f"  Rate limit: {wh.rate_limit}/min")
        print(f"  Capability URL path: /hooks/{wh.id}/{secret}")
        print("  SECRET (shown ONCE, store safely):", secret)
        return 0

    elif action in ("list", "ls"):
        hooks = store.list_all()
        if not hooks:
            print("No webhooks registered.")
            return 0
        print(f"{'ID':<20} {'ACTIVE':<8} {'DELIVERY':<10} {'RATE':<8} {'EVENTS':<20} {'DESC'}")
        print("-" * 75)
        for h in hooks:
            evs = ",".join(h.event_types) if h.event_types else "*"
            print(f"{h.id:<20} {str(h.active):<8} {h.delivery:<10} {h.rate_limit:<8} {evs:<20} {h.description}")
        return 0

    elif action == "revoke":
        ok = store.revoke(args.webhook_id)
        if ok:
            print(f"Webhook {args.webhook_id} revoked.")
            return 0
        else:
            print(f"Webhook {args.webhook_id} not found.")
            return 1

    return 0
