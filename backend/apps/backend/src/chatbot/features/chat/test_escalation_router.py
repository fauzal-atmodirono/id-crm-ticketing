from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.escalation_router import build_escalation_router
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"proton_backend_key": "test-key-123"}
    base.update(kw)
    return Settings(_env_file=None, **base)


def _client(notifier: AsyncMock, cw_response: dict | None, settings: Settings) -> TestClient:
    async def _fake_cw(method: str, path: str, payload: Any = None) -> dict | None:
        return cw_response

    app = FastAPI()
    app.include_router(build_escalation_router(notifier, _fake_cw, settings))
    return TestClient(app)


def test_notify_rejects_missing_api_key() -> None:
    notifier = AsyncMock()
    client = _client(notifier, {}, _settings())
    resp = client.post("/escalation/notify", json={"conversation_id": "9", "title": "t", "body": "b"})
    assert resp.status_code == 401
    notifier.notify_email_channel_escalation.assert_not_called()


def test_notify_rejects_wrong_api_key() -> None:
    notifier = AsyncMock()
    client = _client(notifier, {}, _settings())
    resp = client.post(
        "/escalation/notify",
        headers={"x-api-key": "wrong"},
        json={"conversation_id": "9", "title": "t", "body": "b"},
    )
    assert resp.status_code == 401


def test_notify_resolves_customer_email_and_calls_notifier() -> None:
    notifier = AsyncMock()
    cw_response = {"meta": {"sender": {"email": "alex@customer.example"}}}
    client = _client(notifier, cw_response, _settings())
    resp = client.post(
        "/escalation/notify",
        headers={"x-api-key": "test-key-123"},
        json={
            "conversation_id": "9",
            "title": "Late delivery",
            "body": "details",
            "department": "dept_apps",
            "dealer": "kl_pj",
        },
    )
    assert resp.status_code == 200
    notifier.notify_email_channel_escalation.assert_awaited_once_with(
        conv_id="9",
        title="Late delivery",
        body="details",
        department="dept_apps",
        dealer="kl_pj",
        customer_email="alex@customer.example",
    )


def test_notify_handles_missing_customer_email() -> None:
    notifier = AsyncMock()
    client = _client(notifier, {"meta": {}}, _settings())
    resp = client.post(
        "/escalation/notify",
        headers={"x-api-key": "test-key-123"},
        json={"conversation_id": "9", "title": "t", "body": "b"},
    )
    assert resp.status_code == 200
    notifier.notify_email_channel_escalation.assert_awaited_once_with(
        conv_id="9", title="t", body="b", department=None, dealer=None, customer_email=None,
    )
