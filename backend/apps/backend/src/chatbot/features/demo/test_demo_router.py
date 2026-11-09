"""/demo/* routes for the test console's Demo tab."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.demo.conftest import FakeChatwoot
from chatbot.features.demo.crm import DemoCrmClient
from chatbot.features.demo.router import build_demo_router
from chatbot.features.demo.seeder import DemoSeeder
from chatbot.platform.config import Settings

HDR = {"X-Demo-Console": "1"}


@pytest.fixture
def client(settings: Settings, fake: FakeChatwoot) -> Iterator[TestClient]:
    @asynccontextmanager
    async def factory() -> Any:
        crm = DemoCrmClient(settings, transport=httpx.MockTransport(fake.handler))
        try:
            yield DemoSeeder(settings, crm)
        finally:
            await crm.aclose()

    app = FastAPI()
    app.include_router(build_demo_router(settings, factory))
    with TestClient(app) as c:
        yield c


def test_personas_lists_every_persona(client: TestClient) -> None:
    res = client.get("/demo/personas")
    assert res.status_code == 200
    body = res.json()
    assert [p["id"] for p in body["personas"]] == [
        "ahmad",
        "nurul",
        "lim",
        "farid",
        "new",
        "unknown",
    ]
    assert body["target"] == {"account_id": 7, "inbox_id": 42}


def test_seed_then_reset(client: TestClient, fake: FakeChatwoot) -> None:
    res = client.post("/demo/seed", headers=HDR)
    assert res.status_code == 200, res.text
    assert sorted(res.json()["created_contacts"]) == ["ahmad", "farid", "lim", "nurul"]
    res = client.post("/demo/reset", headers=HDR)
    assert res.status_code == 200, res.text
    assert res.json()["removed"]["contacts"] == 4
    assert len(fake.contacts) == 4


def test_writes_need_the_console_header(client: TestClient, fake: FakeChatwoot) -> None:
    for path in ("/demo/seed", "/demo/reset"):
        assert client.post(path).status_code == 403
    assert fake.mutations() == []


def test_foreign_origin_is_refused(client: TestClient, fake: FakeChatwoot) -> None:
    res = client.post("/demo/seed", headers={**HDR, "Origin": "https://evil.example"})
    assert res.status_code == 403
    assert (
        client.get("/demo/personas", headers={"Origin": "https://evil.example"}).status_code == 403
    )
    assert fake.mutations() == []


def test_allowed_origin_passes(client: TestClient, settings: Settings) -> None:
    origin = settings.frontend_origins[0]
    assert client.post("/demo/seed", headers={**HDR, "Origin": origin}).status_code == 200


def test_safety_refusal_is_a_409(client: TestClient, fake: FakeChatwoot) -> None:
    fake.inbox_channel = "Channel::Whatsapp"
    res = client.post("/demo/seed", headers=HDR)
    assert res.status_code == 409
    assert "Channel::Api" in res.json()["detail"]
    assert fake.mutations() == []


def test_chatwoot_failure_is_a_502(client: TestClient, fake: FakeChatwoot) -> None:
    def broken(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    fake.handler = broken  # type: ignore[method-assign]
    assert client.get("/demo/personas").status_code == 502
