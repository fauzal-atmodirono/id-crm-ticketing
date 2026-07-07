"""Tests for POST /webhooks/chatwoot/bot: HMAC verification against
CHATWOOT_BOT_SECRET (distinct from the account webhook secret), delivery
dedupe, and background-dispatch to `orchestrator.handle_bot_event`.

Same signature scheme as `/webhooks/chatwoot` (see test_chatwoot_router.py)
but signed with the bot secret -- this mirrors Chatwoot's own
`Webhooks::Trigger#request_headers`, which signs agent-bot webhooks with the
bot's own `secret`.
"""

import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services import orchestrator

BOT_SECRET = "test-chatwoot-bot-secret"  # matches conftest env


def _sign(body: bytes, timestamp: str | None = None) -> dict:
    timestamp = timestamp or str(int(time.time()))
    mac = hmac.new(BOT_SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return {
        "X-Chatwoot-Signature": f"sha256={mac.hexdigest()}",
        "X-Chatwoot-Timestamp": timestamp,
    }


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_invalid_signature_is_rejected(client):
    body = json.dumps({"event": "message_created"}).encode()
    response = await client.post(
        "/webhooks/chatwoot/bot",
        content=body,
        headers={
            "X-Chatwoot-Signature": "sha256=deadbeef",
            "X-Chatwoot-Timestamp": "1700000000",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


async def test_account_webhook_secret_does_not_validate_bot_route(client):
    """The bot route must verify against CHATWOOT_BOT_SECRET, not
    CHATWOOT_WEBHOOK_SECRET -- signing with the wrong secret must fail."""
    body = json.dumps({"event": "message_created"}).encode()
    timestamp = str(int(time.time()))
    mac = hmac.new(
        b"test-chatwoot-webhook-secret", f"{timestamp}.".encode() + body, hashlib.sha256
    )
    headers = {
        "X-Chatwoot-Signature": f"sha256={mac.hexdigest()}",
        "X-Chatwoot-Timestamp": timestamp,
        "Content-Type": "application/json",
    }

    response = await client.post("/webhooks/chatwoot/bot", content=body, headers=headers)

    assert response.status_code == 401


async def test_valid_signature_dispatches_to_orchestrator(client, monkeypatch):
    received = {}

    async def fake_handle_bot_event(payload):
        received["payload"] = payload

    monkeypatch.setattr(orchestrator, "handle_bot_event", fake_handle_bot_event)

    body = json.dumps(
        {"event": "message_created", "id": 1, "conversation": {"id": 42, "status": "pending"}}
    ).encode()
    headers = _sign(body)
    headers["Content-Type"] = "application/json"
    headers["X-Chatwoot-Delivery"] = "bot-delivery-1"

    response = await client.post("/webhooks/chatwoot/bot", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert received["payload"]["conversation"]["id"] == 42


async def test_duplicate_delivery_id_is_a_noop_second_time(client, monkeypatch):
    call_count = {"n": 0}

    async def fake_handle_bot_event(payload):
        call_count["n"] += 1

    monkeypatch.setattr(orchestrator, "handle_bot_event", fake_handle_bot_event)

    body = json.dumps({"event": "message_created", "id": 2}).encode()
    headers = _sign(body)
    headers["Content-Type"] = "application/json"
    headers["X-Chatwoot-Delivery"] = "bot-delivery-dup"

    r1 = await client.post("/webhooks/chatwoot/bot", content=body, headers=headers)
    r2 = await client.post("/webhooks/chatwoot/bot", content=body, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert call_count["n"] == 1
