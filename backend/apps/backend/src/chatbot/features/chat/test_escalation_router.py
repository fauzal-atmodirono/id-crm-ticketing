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
    notifier.notify_escalation.assert_not_called()


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
    notifier.notify_escalation.assert_awaited_once_with(
        conv_id="9",
        title="Late delivery",
        body="details",
        department="dept_apps",
        dealer="kl_pj",
        customer_email="alex@customer.example",
        ack_transport="email",
        customer_subject=None,
        customer_in_reply_to=None,
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
    notifier.notify_escalation.assert_awaited_once_with(
        conv_id="9", title="t", body="b", department=None, dealer=None, customer_email=None,
        ack_transport="email", customer_subject=None, customer_in_reply_to=None,
    )


# --- FIX-58: idempotent notify -------------------------------------------------

_H = {"x-api-key": "test-key-123"}


def _notify(client: TestClient, **over: Any):
    body = {"conversation_id": "9", "title": "t", "body": "b", "department": "aftersales"}
    body.update(over)
    return client.post("/escalation/notify", headers=_H, json=body)


def test_notify_twice_same_conv_sends_once() -> None:
    notifier = AsyncMock()
    client = _client(notifier, {"meta": {}}, _settings())
    assert _notify(client).status_code == 200
    r2 = _notify(client)
    assert r2.status_code == 200
    assert r2.json() == {"status": "ok"}
    assert notifier.notify_escalation.await_count == 1


def test_notify_after_window_sends_again(monkeypatch) -> None:
    from chatbot.features.chat import escalation_router as mod

    notifier = AsyncMock()
    client = _client(notifier, {"meta": {}}, _settings())
    clock = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    _notify(client)
    clock[0] += mod.NOTIFY_IDEMPOTENCY_WINDOW_SECONDS + 1
    _notify(client)
    assert notifier.notify_escalation.await_count == 2


def test_different_step_sends() -> None:
    notifier = AsyncMock()
    client = _client(notifier, {"meta": {}}, _settings())
    _notify(client)
    _notify(client, dealer="petaling_jaya")
    _notify(client, conversation_id="10")
    assert notifier.notify_escalation.await_count == 3


def test_failed_notify_is_retried_not_cached() -> None:
    notifier = AsyncMock()
    notifier.notify_escalation.side_effect = [RuntimeError("boom"), None]
    client = TestClient(
        _client(notifier, {"meta": {}}, _settings()).app, raise_server_exceptions=False
    )
    assert _notify(client).status_code == 500
    assert _notify(client).status_code == 200
    assert notifier.notify_escalation.await_count == 2


def test_retry_while_first_call_in_flight_joins_it() -> None:
    import asyncio

    import httpx

    gate = asyncio.Event()
    calls = 0

    async def _slow(**_: Any) -> None:
        nonlocal calls
        calls += 1
        await gate.wait()

    notifier = AsyncMock()
    notifier.notify_escalation.side_effect = _slow

    async def _fake_cw(method: str, path: str, payload: Any = None) -> dict | None:
        return {"meta": {}}

    app = FastAPI()
    app.include_router(build_escalation_router(notifier, _fake_cw, _settings()))

    async def _run() -> list[int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            body = {"conversation_id": "9", "title": "t", "body": "b"}
            t1 = asyncio.create_task(c.post("/escalation/notify", headers=_H, json=body))
            await asyncio.sleep(0.05)
            t2 = asyncio.create_task(c.post("/escalation/notify", headers=_H, json=body))
            await asyncio.sleep(0.05)
            gate.set()
            return [(await t1).status_code, (await t2).status_code]

    assert asyncio.run(_run()) == [200, 200]
    assert calls == 1


# --- FIX-57: contacts cache ----------------------------------------------------


def test_contacts_served_from_cache_within_ttl() -> None:
    from chatbot.features.chat.pic_store import PicRecord

    pic_store = AsyncMock()
    pic_store.list_all.return_value = [
        PicRecord(department="sales", pic_name="A", pic_email="a@x.example", pic_whatsapp="")
    ]

    async def _cw(method: str, path: str, payload: Any = None) -> dict | None:
        return {}

    app = FastAPI()
    app.include_router(build_escalation_router(AsyncMock(), _cw, _settings(), pic_store=pic_store))
    client = TestClient(app)
    r1 = client.get("/escalation/contacts", headers=_H)
    r2 = client.get("/escalation/contacts", headers=_H)
    assert r1.json() == r2.json()
    assert r1.json()["contacts"][0]["email"] == "a@x.example"
    assert pic_store.list_all.await_count == 1
