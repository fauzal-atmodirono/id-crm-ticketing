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
from chatbot.features.chat.sla_policy_db import build_engine as build_sla_policy_engine
from chatbot.features.chat.sla_policy_db import (
    build_session_maker as build_sla_policy_session_maker,
)
from chatbot.features.chat.sla_policy_db import init_sla_policy_db
from chatbot.features.chat.sla_policy_repository import SlaPolicyRepository
from chatbot.features.chat.sla_policy_router import build_sla_policy_router
from chatbot.platform.config import get_settings


async def _build_repos(tmp_path, name: str):
    authz_engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_authz.db")
    await init_authz_db(authz_engine)
    authz_repo = AuthzRepository(build_authz_session_maker(authz_engine))

    sla_engine = build_sla_policy_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_sla.db")
    await init_sla_policy_db(sla_engine)
    sla_repo = SlaPolicyRepository(build_sla_policy_session_maker(sla_engine))

    return authz_repo, sla_repo


def _app_with_router(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.asyncio
async def test_get_default_requires_sla_manage_permission(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo, sla_repo = await _build_repos(tmp_path, "no_perm")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=9, role_id="agent")  # lacks sla.manage

    respx_mock.get(f"{settings.chatwoot_api_url}/auth/validate_token").mock(
        return_value=httpx.Response(200, json={"data": {"id": 9}})
    )
    validator = TokenValidator(settings)
    router = build_sla_policy_router(sla_repo, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get(
        "/admin/sla-policy/default", headers={"x-chatwoot-access-token": "tok-abc", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_get_default_returns_empty_policy_when_unset(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo, sla_repo = await _build_repos(tmp_path, "empty")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=10, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/auth/validate_token").mock(
        return_value=httpx.Response(200, json={"data": {"id": 10}})
    )
    validator = TokenValidator(settings)
    router = build_sla_policy_router(sla_repo, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get(
        "/admin/sla-policy/default", headers={"x-chatwoot-access-token": "tok-abc", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "response_hours": None,
        "resolution_hours": None,
        "ack_minutes_by_channel_json": None,
        "pic_whatsapp": None,
        "engine_enabled": None,
    }


@pytest.mark.asyncio
async def test_put_then_get_default_roundtrips(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo, sla_repo = await _build_repos(tmp_path, "roundtrip_default")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=11, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/auth/validate_token").mock(
        return_value=httpx.Response(200, json={"data": {"id": 11}})
    )
    validator = TokenValidator(settings)
    router = build_sla_policy_router(sla_repo, authz_repo, validator, settings)
    client = _app_with_router(router)
    headers = {"x-chatwoot-access-token": "tok-abc", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"}

    put_res = client.put(
        "/admin/sla-policy/default", json={"response_hours": 4.0}, headers=headers
    )
    assert put_res.status_code == 200
    assert put_res.json()["response_hours"] == 4.0

    get_res = client.get("/admin/sla-policy/default", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["response_hours"] == 4.0


@pytest.mark.asyncio
async def test_put_then_get_inbox_roundtrips(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo, sla_repo = await _build_repos(tmp_path, "roundtrip_inbox")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=12, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/auth/validate_token").mock(
        return_value=httpx.Response(200, json={"data": {"id": 12}})
    )
    validator = TokenValidator(settings)
    router = build_sla_policy_router(sla_repo, authz_repo, validator, settings)
    client = _app_with_router(router)
    headers = {"x-chatwoot-access-token": "tok-abc", "x-chatwoot-client": "client-1", "x-chatwoot-uid": "uid-1"}

    put_res = client.put(
        "/admin/sla-policy/inbox/42",
        json={"resolution_hours": 12.0, "engine_enabled": False},
        headers=headers,
    )
    assert put_res.status_code == 200
    assert put_res.json()["resolution_hours"] == 12.0
    assert put_res.json()["engine_enabled"] is False

    get_res = client.get("/admin/sla-policy/inbox/42", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["resolution_hours"] == 12.0
    assert get_res.json()["engine_enabled"] is False

    # A different, untouched inbox stays empty.
    other_res = client.get("/admin/sla-policy/inbox/99", headers=headers)
    assert other_res.status_code == 200
    assert other_res.json()["resolution_hours"] is None


@pytest.mark.asyncio
async def test_rbac_disabled_falls_back_to_shared_secret(tmp_path):
    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "faq_admin_api_key": "secret123"}
    )
    authz_repo, sla_repo = await _build_repos(tmp_path, "disabled")
    validator = TokenValidator(settings)
    router = build_sla_policy_router(sla_repo, authz_repo, validator, settings)
    client = _app_with_router(router)

    assert client.get("/admin/sla-policy/default").status_code == 401
    assert (
        client.get(
            "/admin/sla-policy/default", headers={"x-api-key": "wrong"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/admin/sla-policy/default", headers={"x-api-key": "secret123"}
        ).status_code
        == 200
    )
