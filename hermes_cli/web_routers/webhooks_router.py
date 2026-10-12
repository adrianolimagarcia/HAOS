"""FastAPI Router for Webhook Ingress (Capability URLs).

Route: POST /hooks/wh_{webhook_id}/{secret}
"""

from __future__ import annotations

from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Request, Response

from hermes.platform.webhooks.ingress import WebhookIngressService
from hermes.platform.webhooks.store import get_webhook_store

router = APIRouter(tags=["webhooks"])
_ingress_service = WebhookIngressService()


def get_ingress_service() -> WebhookIngressService:
    return _ingress_service


def set_ingress_service(service: WebhookIngressService) -> None:
    global _ingress_service
    _ingress_service = service


@router.post("/hooks/{webhook_id}/{secret}")
async def handle_webhook(webhook_id: str, secret: str, request: Request) -> Dict[str, Any]:
    service = get_ingress_service()
    # Step 1: Authenticate BEFORE reading or buffering body
    authenticated, wh = service.authenticate_request(webhook_id, secret)
    if not authenticated or wh is None:
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")

    # Step 2: Read payload only after successful capability auth
    try:
        body = await request.json()
    except Exception:
        body = {}

    headers = dict(request.headers)
    result = service.process_payload(wh, body, headers=headers)
    if result.status_code == 429:
        raise HTTPException(status_code=429, detail=result.message)

    return result.to_dict()
