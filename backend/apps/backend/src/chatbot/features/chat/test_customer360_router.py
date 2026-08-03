from __future__ import annotations

from unittest.mock import AsyncMock

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
from chatbot.features.chat.customer360_router import build_customer360_router
from chatbot.features.rsa.rsa_repository import InMemoryRsaRepository
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


def _fake_chatwoot(*, contacts=None, contact_conversations=None, all_conversations=None):
    """AsyncMock stand-in for ChatwootAdapter exposing only the three public
    Customer 360 read methods the router calls -- search_contacts,
    list_contact_conversations, list_conversations."""
    chatwoot = AsyncMock()
    chatwoot.search_contacts.return_value = contacts or []
    chatwoot.list_contact_conversations.return_value = contact_conversations or []
    chatwoot.list_conversations.return_value = all_conversations or []
    return chatwoot


async def _authorized(tmp_path, name: str, user_id: int):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, name)
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=user_id, role_id="administrator")
    validator = TokenValidator(settings)
    return settings, authz_repo, validator


@pytest.mark.asyncio
async def test_search_requires_customer360_view_permission(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "no_perm")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=9, role_id="agent")  # lacks customer360.view

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )
    validator = TokenValidator(settings)
    router = build_customer360_router(
        _fake_chatwoot(), InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=+60123456789", headers=HEADERS)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(tmp_path):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "unauth")
    await seed_defaults(authz_repo)
    validator = TokenValidator(settings)
    router = build_customer360_router(
        _fake_chatwoot(), InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=+60123456789")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_phone_query_searches_contact_and_conversations(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "phone_ok", 10)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 10})
    )
    contact = {"id": 77, "name": "Ali", "phone_number": "+60123456789"}
    conversations = [{"id": 501, "status": "resolved", "inbox_id": 1}]
    chatwoot = _fake_chatwoot(contacts=[contact], contact_conversations=conversations)
    rsa_repo = InMemoryRsaRepository()
    router = build_customer360_router(chatwoot, rsa_repo, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=%2B60123456789", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["contact"] == contact
    assert body["conversations"] == conversations
    assert body["rsa_incidents"] == []
    chatwoot.search_contacts.assert_awaited_once_with("+60123456789")
    chatwoot.list_contact_conversations.assert_awaited_once_with(77)
    chatwoot.list_conversations.assert_not_awaited()


@pytest.mark.asyncio
async def test_phone_query_picks_exact_digits_match_over_first_result(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "phone_pick", 14)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 14})
    )
    decoy = {"id": 1, "name": "Decoy", "phone_number": "+60111111111"}
    wanted = {"id": 2, "name": "Wanted", "phone_number": "60-1234-5678"}
    chatwoot = _fake_chatwoot(contacts=[decoy, wanted], contact_conversations=[])
    router = build_customer360_router(
        chatwoot, InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=%2B60%201234%205678", headers=HEADERS)
    assert res.status_code == 200
    assert res.json()["contact"] == wanted
    chatwoot.list_contact_conversations.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_vehicle_query_searches_rsa_incidents_and_conversations(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "vehicle_ok", 11)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 11})
    )
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(
        incident_date="2026-08-01", vehicle_no="ABC1234", vehicle_model="Camry", cause="flat tyre"
    )
    await rsa_repo.create_incident(
        incident_date="2026-08-02", vehicle_no="XYZ9999", vehicle_model="Corolla", cause="battery"
    )
    matching_conv = {
        "id": 42,
        "status": "open",
        "inbox_id": 1,
        "custom_attributes": {"vehicle_model": "Toyota Camry"},
    }
    other_conv = {
        "id": 43,
        "status": "open",
        "inbox_id": 1,
        "custom_attributes": {"vehicle_model": "Honda Civic"},
    }
    chatwoot = _fake_chatwoot(all_conversations=[matching_conv, other_conv])
    router = build_customer360_router(chatwoot, rsa_repo, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=camry", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["contact"] is None
    assert len(body["rsa_incidents"]) == 0  # "camry" doesn't substring-match a vehicle_no
    assert body["conversations"] == [matching_conv]
    chatwoot.search_contacts.assert_not_awaited()

    res2 = client.get("/admin/customer360/search?q=ABC1234", headers=HEADERS)
    body2 = res2.json()
    assert len(body2["rsa_incidents"]) == 1
    assert body2["rsa_incidents"][0]["vehicle_no"] == "ABC1234"


@pytest.mark.asyncio
async def test_no_match_returns_empty_lists_not_error(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "no_match", 12)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 12})
    )
    chatwoot = _fake_chatwoot()
    router = build_customer360_router(
        chatwoot, InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=nomatchvehicle", headers=HEADERS)
    assert res.status_code == 200
    assert res.json() == {"contact": None, "conversations": [], "rsa_incidents": []}


@pytest.mark.asyncio
async def test_phone_query_no_contact_found_returns_empty(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "phone_no_match", 13)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 13})
    )
    chatwoot = _fake_chatwoot(contacts=[])
    router = build_customer360_router(
        chatwoot, InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=+60199999999", headers=HEADERS)
    assert res.status_code == 200
    assert res.json() == {"contact": None, "conversations": [], "rsa_incidents": []}
    chatwoot.list_contact_conversations.assert_not_awaited()


@pytest.mark.asyncio
async def test_rbac_disabled_falls_back_to_shared_secret(tmp_path):
    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "faq_admin_api_key": "secret123"}
    )
    authz_repo = await _build_authz_repo(tmp_path, "disabled")
    validator = TokenValidator(settings)
    chatwoot = _fake_chatwoot(contacts=[{"id": 5, "phone_number": "+60100000000"}])
    router = build_customer360_router(
        chatwoot, InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    assert client.get("/admin/customer360/search?q=%2B60100000000").status_code == 401
    assert (
        client.get(
            "/admin/customer360/search?q=%2B60100000000", headers={"x-api-key": "wrong"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/admin/customer360/search?q=%2B60100000000", headers={"x-api-key": "secret123"}
        ).status_code
        == 200
    )


@pytest.mark.asyncio
async def test_query_too_short_returns_422(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "too_short", 15)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 15})
    )
    router = build_customer360_router(
        _fake_chatwoot(), InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=a", headers=HEADERS)
    assert res.status_code == 422
