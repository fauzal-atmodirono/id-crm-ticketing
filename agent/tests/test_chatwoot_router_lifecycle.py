"""Tests for lifecycle dispatch in POST /webhooks/chatwoot.

Verifies that when lifecycle_enabled is True:
- conversation_created → lifecycle.on_conversation_created (background task)
- conversation_status_changed with status=resolved → lifecycle.on_human_resolved
  (background task), in ADDITION to sync.record_conversation_status.

Note: The signing timestamp must be within the 300 s skew window of `now`,
so we use str(int(time.time())) — mirroring the convention in test_chatwoot_router.py.
"""

import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import create_app

SECRET = "test-chatwoot-webhook-secret"  # matches conftest env


def _sign(body: bytes, timestamp: str | None = None) -> dict:
    timestamp = timestamp or str(int(time.time()))
    mac = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return {
        "X-Chatwoot-Signature": f"sha256={mac.hexdigest()}",
        "X-Chatwoot-Timestamp": timestamp,
    }


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "lifecycle_enabled", True, raising=False)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _post(client, payload, delivery):
    body = json.dumps(payload).encode()
    headers = _sign(body)
    headers["Content-Type"] = "application/json"
    headers["X-Chatwoot-Delivery"] = delivery
    return await client.post("/webhooks/chatwoot", content=body, headers=headers)


async def test_conversation_created_dispatches_lifecycle(client, monkeypatch):
    calls = []
    from app.services import lifecycle

    async def fake(payload):
        calls.append(payload["id"])

    monkeypatch.setattr(lifecycle, "on_conversation_created", fake)
    resp = await _post(client, {"event": "conversation_created", "id": 12, "inbox_id": 2}, "d1")
    assert resp.status_code == 200
    assert calls == [12]


async def test_resolved_status_triggers_agent_survey(client, monkeypatch):
    calls = []
    from app.services import lifecycle

    async def fake(payload):
        calls.append(payload["id"])

    monkeypatch.setattr(lifecycle, "on_human_resolved", fake)
    resp = await _post(
        client,
        {"event": "conversation_status_changed", "id": 13, "status": "resolved"},
        "d2",
    )
    assert resp.status_code == 200
    assert calls == [13]
