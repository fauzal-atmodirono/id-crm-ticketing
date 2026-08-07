from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.db import build_engine as build_authz_engine
from chatbot.features.authz.db import build_session_maker as build_authz_session_maker
from chatbot.features.authz.db import init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults
from chatbot.features.chat.pic_admin_router import build_pic_admin_router
from chatbot.features.chat.pic_store import DealerStore, PicStore
from chatbot.platform.config import get_settings

HEADERS = {
    "x-chatwoot-access-token": "tok-abc",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "uid-1",
}


async def _build_authz_repo(tmp_path, name: str) -> AuthzRepository:
    authz_engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_authz.db")
    await init_authz_db(authz_engine)
    return AuthzRepository(build_authz_session_maker(authz_engine))


def _app_with_router(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class _FakeDoc:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store = store
        self._key = key

    def get(self) -> MagicMock:
        snap = MagicMock()
        data = self._store.get(self._key)
        snap.exists = data is not None
        snap.to_dict.return_value = data or {}
        return snap

    def set(self, data: dict[str, Any]) -> None:
        self._store[self._key] = data

    def delete(self) -> None:
        self._store.pop(self._key, None)


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, key: str) -> _FakeDoc:
        return _FakeDoc(self._store, key)

    def stream(self) -> list[MagicMock]:
        snaps = []
        for data in self._store.values():
            snap = MagicMock()
            snap.to_dict.return_value = data
            snaps.append(snap)
        return snaps


class _FakeFirestoreClient:
    """In-memory stand-in for google.cloud.firestore.Client, keyed by
    collection name so PicStore and DealerStore (which use different
    collections) don't clobber each other within one test.
    """

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._collections.setdefault(name, {}))


def _build_stores(settings) -> tuple[PicStore, DealerStore]:
    return PicStore(settings), DealerStore(settings)


@pytest.mark.asyncio
async def test_list_pics_requires_escalation_manage_permission(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "no_perm")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=9, role_id="agent")  # lacks escalation.manage

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )
    validator = TokenValidator(settings)
    pic_store, dealer_store = _build_stores(settings)
    router = build_pic_admin_router(pic_store, dealer_store, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get("/admin/escalation/pics", headers=HEADERS)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(tmp_path):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "unauth")
    await seed_defaults(authz_repo)
    validator = TokenValidator(settings)
    pic_store, dealer_store = _build_stores(settings)
    router = build_pic_admin_router(pic_store, dealer_store, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get("/admin/escalation/pics")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_authorized_user_can_list_upsert_delete_pic(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "pic_crud")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=10, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 10})
    )
    validator = TokenValidator(settings)
    pic_store, dealer_store = _build_stores(settings)
    router = build_pic_admin_router(pic_store, dealer_store, authz_repo, validator, settings)
    client = _app_with_router(router)

    with patch("chatbot.features.chat.pic_store.firestore.Client", autospec=True) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        empty_res = client.get("/admin/escalation/pics", headers=HEADERS)
        assert empty_res.status_code == 200
        assert empty_res.json() == {"pics": []}

        put_res = client.put(
            "/admin/escalation/pics/sales",
            json={
                "pic_name": "John Doe",
                "pic_email": "john@example.com",
                "pic_whatsapp": "+1234567890",
                "cc_emails": ["cc@example.com"],
            },
            headers=HEADERS,
        )
        assert put_res.status_code == 200
        assert put_res.json() == {"department": "sales", "status": "ok"}

        list_res = client.get("/admin/escalation/pics", headers=HEADERS)
        assert list_res.status_code == 200
        pics = list_res.json()["pics"]
        assert len(pics) == 1
        assert pics[0]["department"] == "sales"
        assert pics[0]["pic_email"] == "john@example.com"

        delete_res = client.delete("/admin/escalation/pics/sales", headers=HEADERS)
        assert delete_res.status_code == 200
        assert delete_res.json() == {"department": "sales", "status": "ok"}

        after_delete_res = client.get("/admin/escalation/pics", headers=HEADERS)
        assert after_delete_res.json() == {"pics": []}


@pytest.mark.asyncio
async def test_authorized_user_can_list_upsert_delete_dealer(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "dealer_crud")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=11, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 11})
    )
    validator = TokenValidator(settings)
    pic_store, dealer_store = _build_stores(settings)
    router = build_pic_admin_router(pic_store, dealer_store, authz_repo, validator, settings)
    client = _app_with_router(router)

    with patch("chatbot.features.chat.pic_store.firestore.Client", autospec=True) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        put_res = client.put(
            "/admin/escalation/dealers/acme",
            json={"emails": ["acme@example.com", "second@example.com"]},
            headers=HEADERS,
        )
        assert put_res.status_code == 200
        assert put_res.json() == {"dealer": "acme", "status": "ok"}

        list_res = client.get("/admin/escalation/dealers", headers=HEADERS)
        assert list_res.status_code == 200
        dealers = list_res.json()["dealers"]
        assert dealers == [
            {"dealer": "acme", "emails": ["acme@example.com", "second@example.com"]}
        ]

        delete_res = client.delete("/admin/escalation/dealers/acme", headers=HEADERS)
        assert delete_res.status_code == 200
        assert delete_res.json() == {"dealer": "acme", "status": "ok"}

        after_delete_res = client.get("/admin/escalation/dealers", headers=HEADERS)
        assert after_delete_res.json() == {"dealers": []}


@pytest.mark.asyncio
async def test_upsert_pic_missing_required_fields_returns_422(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "pic_422")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=12, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 12})
    )
    validator = TokenValidator(settings)
    pic_store, dealer_store = _build_stores(settings)
    router = build_pic_admin_router(pic_store, dealer_store, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.put("/admin/escalation/pics/sales", json={}, headers=HEADERS)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_upsert_dealer_rejects_wrong_field_type_returns_422(tmp_path, respx_mock):
    """`emails`/`email` are both optional now (a dealer group may start with
    no members), so an empty body is valid -- but a wrong-typed `emails`
    still fails Pydantic validation."""
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "dealer_422")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=13, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 13})
    )
    validator = TokenValidator(settings)
    pic_store, dealer_store = _build_stores(settings)
    router = build_pic_admin_router(pic_store, dealer_store, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.put(
        "/admin/escalation/dealers/acme", json={"emails": "not-a-list"}, headers=HEADERS
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_rbac_disabled_falls_back_to_shared_secret(tmp_path):
    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "faq_admin_api_key": "secret123"}
    )
    authz_repo = await _build_authz_repo(tmp_path, "disabled")
    validator = TokenValidator(settings)
    pic_store, dealer_store = _build_stores(settings)
    router = build_pic_admin_router(pic_store, dealer_store, authz_repo, validator, settings)
    client = _app_with_router(router)

    assert client.get("/admin/escalation/pics").status_code == 401
    assert (
        client.get("/admin/escalation/pics", headers={"x-api-key": "wrong"}).status_code == 401
    )
    with patch("chatbot.features.chat.pic_store.firestore.Client", autospec=True) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()
        assert (
            client.get(
                "/admin/escalation/pics", headers={"x-api-key": "secret123"}
            ).status_code
            == 200
        )
