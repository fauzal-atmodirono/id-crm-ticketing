"""Tests for POST /webhooks/zammad: HMAC verification, delivery dedupe, and
loop-prevention (dropping payloads authored by the integration user)."""

import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services import sync

TOKEN = "test-zammad-webhook-secret"  # matches conftest env
INTEGRATION_LOGIN = "integration@local"  # matches conftest env


def _sign(body: bytes) -> str:
    mac = hmac.new(TOKEN.encode(), body, hashlib.sha1)
    return f"sha1={mac.hexdigest()}"


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_invalid_signature_is_rejected(client):
    body = json.dumps({"ticket": {"id": 1, "state": "open"}}).encode()
    response = await client.post(
        "/webhooks/zammad",
        content=body,
        headers={
            "X-Hub-Signature": "sha1=deadbeef",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


async def test_valid_signature_is_accepted_and_dispatches(client, monkeypatch):
    received = {}

    async def fake_on_ticket_event(payload):
        received["payload"] = payload

    monkeypatch.setattr(sync, "on_ticket_event", fake_on_ticket_event)

    body = json.dumps(
        {
            "ticket": {"id": 1, "number": "1001", "state": "open"},
            "article": {"created_by": {"login": "agent@example.com"}},
        }
    ).encode()
    headers = {
        "X-Hub-Signature": _sign(body),
        "Content-Type": "application/json",
        "X-Zammad-Delivery": "zdelivery-1",
    }

    response = await client.post("/webhooks/zammad", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert received["payload"]["ticket"]["id"] == 1


async def test_duplicate_delivery_id_is_a_noop_second_time(client, monkeypatch):
    call_count = {"n": 0}

    async def fake_on_ticket_event(payload):
        call_count["n"] += 1

    monkeypatch.setattr(sync, "on_ticket_event", fake_on_ticket_event)

    body = json.dumps(
        {
            "ticket": {"id": 2, "number": "1002", "state": "open"},
            "article": {"created_by": {"login": "agent@example.com"}},
        }
    ).encode()
    headers = {
        "X-Hub-Signature": _sign(body),
        "Content-Type": "application/json",
        "X-Zammad-Delivery": "zdelivery-dup",
    }

    r1 = await client.post("/webhooks/zammad", content=body, headers=headers)
    r2 = await client.post("/webhooks/zammad", content=body, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert call_count["n"] == 1


async def test_self_authored_payload_is_dropped_for_loop_prevention(
    client, monkeypatch
):
    call_count = {"n": 0}

    async def fake_on_ticket_event(payload):
        call_count["n"] += 1

    monkeypatch.setattr(sync, "on_ticket_event", fake_on_ticket_event)

    body = json.dumps(
        {
            "ticket": {"id": 3, "number": "1003", "state": "closed"},
            "article": {"created_by": {"login": INTEGRATION_LOGIN}},
        }
    ).encode()
    headers = {
        "X-Hub-Signature": _sign(body),
        "Content-Type": "application/json",
        "X-Zammad-Delivery": "zdelivery-self",
    }

    response = await client.post("/webhooks/zammad", content=body, headers=headers)

    assert response.status_code == 200
    assert call_count["n"] == 0
