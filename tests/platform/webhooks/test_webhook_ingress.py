"""Tests for Webhook Ingress capability URLs and security invariants."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest
from starlette.testclient import TestClient

from hermes.platform.webhooks.models import (
    Webhook,
    generate_webhook,
    hash_secret,
    verify_secret_hash,
)
from hermes.platform.webhooks.store import WebhookStore
from hermes.platform.webhooks.ingress import (
    WebhookIngressService,
    DeliveryDeduplicator,
    RateLimiter,
)
from hermes_cli.web_server import app
from hermes_cli.web_routers.webhooks_router import set_ingress_service


class TestWebhookSecurityAndStorage:
    def test_secret_is_stored_as_hash_never_plaintext(self):
        wh, raw_secret = generate_webhook(description="test webhook")
        assert wh.secret_hash != raw_secret
        assert len(wh.secret_hash) == 64  # SHA-256 hex digest
        assert wh.secret_hash == hash_secret(raw_secret)
        assert verify_secret_hash(raw_secret, wh.secret_hash) is True
        assert verify_secret_hash("wrong_secret", wh.secret_hash) is False

    def test_store_roundtrip_preserves_hashes(self):
        with TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "webhooks.json"
            store = WebhookStore(file_path=store_file)

            wh, raw_secret = generate_webhook(event_types=["push"], delivery="post")
            store.save(wh)

            loaded_wh = store.get(wh.id)
            assert loaded_wh is not None
            assert loaded_wh.id == wh.id
            assert loaded_wh.secret_hash == wh.secret_hash
            assert loaded_wh.verify_secret(raw_secret) is True
            assert loaded_wh.verify_secret("wrong_secret") is False

            # Verify persisted raw file does not contain raw_secret
            content = store_file.read_text(encoding="utf-8")
            assert raw_secret not in content
            assert wh.secret_hash in content


class TestWebhookIngressLogic:
    def test_invalid_secret_fails_authentication(self):
        wh, raw_secret = generate_webhook(event_types=["push"])
        service = WebhookIngressService()
        service.store._cache[wh.id] = wh
        service.store._loaded = True

        auth_ok, auth_wh = service.authenticate_request(wh.id, "wrong_secret")
        assert auth_ok is False
        assert auth_wh is None

    def test_event_outside_event_types_is_ignored(self):
        wh, raw_secret = generate_webhook(event_types=["github.push"])
        service = WebhookIngressService()

        # Event is "github.issue", not in ["github.push"]
        res = service.process_payload(wh, {"action": "created"}, headers={"x-event-type": "github.issue"})
        assert res.status_code == 200
        assert res.delivered is False
        assert res.ignored is True
        assert "not subscribed" in res.message

    def test_duplicate_delivery_id_processed_only_once(self):
        wh, raw_secret = generate_webhook(event_types=["push"])
        calls = []
        service = WebhookIngressService(turn_runner_cb=lambda ctx: calls.append(ctx))

        headers = {"x-delivery-id": "unique-uuid-12345", "x-event-type": "push"}
        # First call: processed
        res1 = service.process_payload(wh, {"data": "first"}, headers=headers)
        assert res1.status_code == 200
        assert res1.delivered is True
        assert res1.ignored is False
        assert len(calls) == 1

        # Second call with same delivery-id: ignored
        res2 = service.process_payload(wh, {"data": "second"}, headers=headers)
        assert res2.status_code == 200
        assert res2.delivered is False
        assert res2.ignored is True
        assert "Duplicate" in res2.message
        assert len(calls) == 1

    def test_run_mode_enqueues_turn_and_post_mode_only_posts_message(self):
        wh_run, secret_run = generate_webhook(delivery="run")
        wh_post, secret_post = generate_webhook(delivery="post")

        turns_enqueued = []
        messages_posted = []

        service = WebhookIngressService(
            turn_runner_cb=lambda ctx: turns_enqueued.append(ctx),
            message_poster_cb=lambda ctx: messages_posted.append(ctx),
        )

        # Execute run mode
        res_run = service.process_payload(wh_run, {"task": "deploy"})
        assert res_run.delivered is True
        assert res_run.delivery_mode == "run"
        assert len(turns_enqueued) == 1
        assert len(messages_posted) == 0

        # Execute post mode
        res_post = service.process_payload(wh_post, {"msg": "alert"})
        assert res_post.delivered is True
        assert res_post.delivery_mode == "post"
        assert len(turns_enqueued) == 1
        assert len(messages_posted) == 1


class TestWebhookFastAPIEndpoint:
    def test_http_endpoint_auth_and_flow(self):
        with TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "webhooks.json"
            store = WebhookStore(file_path=store_file)
            wh, raw_secret = generate_webhook(event_types=["deploy"], delivery="run")
            store.save(wh)

            turns = []
            service = WebhookIngressService(store=store, turn_runner_cb=lambda ctx: turns.append(ctx))
            set_ingress_service(service)

            client = TestClient(app)

            # 1. Invalid secret returns 401 immediately
            resp_401 = client.post(f"/hooks/{wh.id}/badsecret", json={"test": 1})
            assert resp_401.status_code == 401
            assert resp_401.json()["detail"] == "Invalid webhook credentials"
            assert len(turns) == 0

            # 2. Valid secret processes successfully
            headers = {"x-event-type": "deploy", "x-delivery-id": "deliv-777"}
            resp_200 = client.post(f"/hooks/{wh.id}/{raw_secret}", json={"target": "prod"}, headers=headers)
            assert resp_200.status_code == 200
            data = resp_200.json()
            assert data["delivered"] is True
            assert data["delivery_mode"] == "run"
            assert len(turns) == 1

            # 3. Duplicate delivery-id is ignored
            resp_dup = client.post(f"/hooks/{wh.id}/{raw_secret}", json={"target": "prod"}, headers=headers)
            assert resp_dup.status_code == 200
            assert resp_dup.json()["ignored"] is True
            assert len(turns) == 1
