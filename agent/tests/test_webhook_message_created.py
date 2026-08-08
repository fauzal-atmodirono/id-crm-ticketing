"""The account webhook dispatches message_created to the reply linker."""

import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def _signed(body: bytes):
    ts = str(int(time.time()))
    secret = get_settings().chatwoot_webhook_secret
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "X-Chatwoot-Signature": f"sha256={sig}",
        "X-Chatwoot-Timestamp": ts,
        "X-Chatwoot-Delivery": f"test-{ts}-{hash(body) & 0xFFFF}",
        "Content-Type": "application/json",
    }


def test_message_created_dispatches_to_reply_linker(monkeypatch):
    seen = []

    async def _fake(payload):
        seen.append(payload)

    monkeypatch.setattr(
        "app.services.escalation_replies.maybe_link_escalation_reply", _fake
    )

    body = json.dumps({"event": "message_created", "id": 1}).encode()
    with TestClient(create_app()) as client:
        res = client.post("/webhooks/chatwoot", content=body, headers=_signed(body))

    assert res.status_code == 200
    assert seen and seen[0]["event"] == "message_created"


def test_message_created_dispatches_to_dept_suggestion(monkeypatch):
    seen = []

    async def _fake(payload):
        seen.append(payload)

    monkeypatch.setattr(
        "app.services.dept_suggestion.maybe_suggest_department", _fake
    )

    body = json.dumps({"event": "message_created", "id": 2}).encode()
    with TestClient(create_app()) as client:
        res = client.post("/webhooks/chatwoot", content=body, headers=_signed(body))

    assert res.status_code == 200
    assert seen and seen[0]["event"] == "message_created"
