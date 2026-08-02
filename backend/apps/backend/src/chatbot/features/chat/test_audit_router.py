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
from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.audit_router import build_audit_router
from chatbot.features.chat.ports import AuditEntry
from chatbot.platform.config import get_settings


async def _build_authz_repo(tmp_path, name: str) -> AuthzRepository:
    engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_authz.db")
    await init_authz_db(engine)
    return AuthzRepository(build_authz_session_maker(engine))


def _app_with_router(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


async def _seeded_audit_log() -> InMemoryAuditLog:
    log = InMemoryAuditLog()
    await log.append(
        AuditEntry(
            ticket_id="1",
            session_id="s1",
            actor="alice",
            from_state="open",
            to_state="pending",
            at="2026-08-01T10:00:00Z",
            remark="",
        )
    )
    await log.append(
        AuditEntry(
            ticket_id="2",
            session_id="s2",
            actor="bob",
            from_state="open",
            to_state="pending",
            at="2026-08-01T11:00:00Z",
            remark="",
        )
    )
    return log


@pytest.mark.asyncio
async def test_list_audit_requires_audit_view_permission(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "no_perm")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=21, role_id="agent")  # lacks audit.view

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 21})
    )
    validator = TokenValidator(settings)
    audit_log = await _seeded_audit_log()
    router = build_audit_router(audit_log, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get("/admin/audit", headers={"x-chatwoot-access-token": "tok-abc"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_list_audit_returns_all_entries_when_permitted(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "all_entries")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=22, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 22})
    )
    validator = TokenValidator(settings)
    audit_log = await _seeded_audit_log()
    router = build_audit_router(audit_log, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get("/admin/audit", headers={"x-chatwoot-access-token": "tok-abc"})
    assert res.status_code == 200
    body = res.json()
    assert [entry["actor"] for entry in body["audit"]] == ["bob", "alice"]


@pytest.mark.asyncio
async def test_list_audit_filters_by_actor(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "filter_actor")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=23, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 23})
    )
    validator = TokenValidator(settings)
    audit_log = await _seeded_audit_log()
    router = build_audit_router(audit_log, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get(
        "/admin/audit", params={"actor": "alice"}, headers={"x-chatwoot-access-token": "tok-abc"}
    )
    assert res.status_code == 200
    body = res.json()
    assert [entry["ticket_id"] for entry in body["audit"]] == ["1"]


@pytest.mark.asyncio
async def test_rbac_disabled_falls_back_to_shared_secret(tmp_path):
    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "faq_admin_api_key": "secret123"}
    )
    authz_repo = await _build_authz_repo(tmp_path, "disabled")
    validator = TokenValidator(settings)
    audit_log = await _seeded_audit_log()
    router = build_audit_router(audit_log, authz_repo, validator, settings)
    client = _app_with_router(router)

    assert client.get("/admin/audit").status_code == 401
    assert client.get("/admin/audit", headers={"x-api-key": "wrong"}).status_code == 401
    assert (
        client.get("/admin/audit", headers={"x-api-key": "secret123"}).status_code == 200
    )
