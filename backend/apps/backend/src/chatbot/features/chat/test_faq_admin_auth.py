from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.faq_admin_router import build_faq_admin_router
from chatbot.platform.config import Settings


class _Store:
    async def list_all(self):
        return []


def _client(faq_key: str, proton_key: str) -> TestClient:
    s = Settings(faq_admin_api_key=faq_key, proton_backend_key=proton_key)
    app = FastAPI()
    app.include_router(build_faq_admin_router(_Store(), s))
    return TestClient(app, raise_server_exceptions=False)


def test_proton_key_accepted() -> None:
    c = _client(faq_key="fk", proton_key="pk")
    assert c.get("/kb/faq", headers={"x-api-key": "pk"}).status_code == 200


def test_faq_key_still_accepted() -> None:
    c = _client(faq_key="fk", proton_key="pk")
    assert c.get("/kb/faq", headers={"x-api-key": "fk"}).status_code == 200


def test_wrong_key_rejected() -> None:
    c = _client(faq_key="fk", proton_key="pk")
    assert c.get("/kb/faq", headers={"x-api-key": "nope"}).status_code == 401


def test_empty_keys_reject_everything() -> None:
    c = _client(faq_key="", proton_key="")
    assert c.get("/kb/faq", headers={"x-api-key": ""}).status_code == 401
