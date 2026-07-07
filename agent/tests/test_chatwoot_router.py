"""Tests for POST /webhooks/chatwoot: HMAC verification, delivery dedupe,
and event dispatch to background sync tasks.

Background tasks registered via `BackgroundTasks.add_task` run to
completion before Starlette finishes sending the response, so with
`ASGITransport` the side effects are observable immediately after
`await client.post(...)` returns — no polling needed.
"""

import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services import sync

SECRET = "test-chatwoot-webhook-secret"  # matches conftest env


def _sign(body: bytes, timestamp: str | None = None) -> dict:
    # The router verifies against the real current time (it doesn't accept
    # a `now` override), so the signed timestamp must be "now", not a fixed
    # value that will eventually drift outside max_skew_seconds.
    timestamp = timestamp or str(int(time.time()))
    mac = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
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
    body = json.dumps({"event": "contact_created", "id": 1}).encode()
    response = await client.post(
        "/webhooks/chatwoot",
        content=body,
        headers={
            "X-Chatwoot-Signature": "sha256=deadbeef",
            "X-Chatwoot-Timestamp": "1700000000",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


async def test_valid_signature_is_accepted(client, monkeypatch):
    async def fake_upsert_contact(payload):
        return None

    monkeypatch.setattr(sync, "upsert_contact", fake_upsert_contact)

    body = json.dumps(
        {"event": "contact_created", "id": 1, "email": "a@example.com"}
    ).encode()
    headers = _sign(body)
    headers["Content-Type"] = "application/json"
    headers["X-Chatwoot-Delivery"] = "delivery-accept-1"

    response = await client.post("/webhooks/chatwoot", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_duplicate_delivery_id_is_a_noop_second_time(client, monkeypatch):
    call_count = {"n": 0}

    async def fake_upsert_contact(payload):
        call_count["n"] += 1

    monkeypatch.setattr(sync, "upsert_contact", fake_upsert_contact)

    body = json.dumps(
        {"event": "contact_created", "id": 2, "email": "b@example.com"}
    ).encode()
    headers = _sign(body)
    headers["Content-Type"] = "application/json"
    headers["X-Chatwoot-Delivery"] = "delivery-dup-1"

    r1 = await client.post("/webhooks/chatwoot", content=body, headers=headers)
    r2 = await client.post("/webhooks/chatwoot", content=body, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert call_count["n"] == 1


async def test_missing_delivery_header_always_processes(client, monkeypatch):
    call_count = {"n": 0}

    async def fake_upsert_contact(payload):
        call_count["n"] += 1

    monkeypatch.setattr(sync, "upsert_contact", fake_upsert_contact)

    body = json.dumps(
        {"event": "contact_created", "id": 3, "email": "c@example.com"}
    ).encode()
    headers = _sign(body)
    headers["Content-Type"] = "application/json"

    await client.post("/webhooks/chatwoot", content=body, headers=headers)
    await client.post("/webhooks/chatwoot", content=body, headers=headers)

    assert call_count["n"] == 2


@pytest.mark.parametrize("event", ["contact_created", "contact_updated"])
async def test_contact_events_dispatch_to_upsert_contact(client, monkeypatch, event):
    received = {}

    async def fake_upsert_contact(payload):
        received["payload"] = payload

    monkeypatch.setattr(sync, "upsert_contact", fake_upsert_contact)

    body = json.dumps({"event": event, "id": 5, "email": "d@example.com"}).encode()
    headers = _sign(body)
    headers["Content-Type"] = "application/json"

    await client.post("/webhooks/chatwoot", content=body, headers=headers)

    assert received["payload"]["id"] == 5


async def test_conversation_updated_dispatches_to_maybe_escalate(client, monkeypatch):
    received = {}

    async def fake_maybe_escalate(payload):
        received["payload"] = payload

    monkeypatch.setattr(sync, "maybe_escalate", fake_maybe_escalate)

    body = json.dumps(
        {"event": "conversation_updated", "id": 42, "labels": ["escalate"]}
    ).encode()
    headers = _sign(body)
    headers["Content-Type"] = "application/json"

    await client.post("/webhooks/chatwoot", content=body, headers=headers)

    assert received["payload"]["id"] == 42


@pytest.mark.parametrize(
    "event", ["conversation_status_changed", "conversation_resolved"]
)
async def test_status_events_dispatch_to_record_conversation_status(
    client, monkeypatch, event
):
    received = {}

    async def fake_record(payload):
        received["payload"] = payload

    monkeypatch.setattr(sync, "record_conversation_status", fake_record)

    body = json.dumps({"event": event, "id": 42, "status": "resolved"}).encode()
    headers = _sign(body)
    headers["Content-Type"] = "application/json"

    await client.post("/webhooks/chatwoot", content=body, headers=headers)

    assert received["payload"]["id"] == 42
