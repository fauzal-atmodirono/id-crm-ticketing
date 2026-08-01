import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.router import build_authz_router
from chatbot.features.authz.seed import seed_defaults
from chatbot.platform.config import get_settings


@pytest.fixture
async def client(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/router_test.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=1, role_id="administrator")
    await repo.assign_role(chatwoot_user_id=2, role_id="agent")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        side_effect=lambda request: httpx.Response(
            200, json={"id": 1 if request.headers["api_access_token"] == "admin-tok" else 2}
        )
    )
    validator = TokenValidator(settings)
    app = FastAPI()
    app.include_router(build_authz_router(repo, validator, settings))
    return TestClient(app)


def test_permissions_endpoint_returns_callers_own_permissions(client):
    res = client.get("/authz/permissions", headers={"x-chatwoot-access-token": "agent-tok"})
    assert res.status_code == 200
    assert res.json()["permissions"] == ["knowledge.edit"]


def test_check_endpoint(client):
    res = client.get(
        "/authz/check",
        params={"permission": "sla.manage"},
        headers={"x-chatwoot-access-token": "admin-tok"},
    )
    assert res.json() == {"allowed": True}
    res = client.get(
        "/authz/check",
        params={"permission": "sla.manage"},
        headers={"x-chatwoot-access-token": "agent-tok"},
    )
    assert res.json() == {"allowed": False}


def test_create_role_requires_roles_manage_permission(client):
    res = client.post(
        "/authz/roles",
        json={"id": "leader", "name": "Team Leader", "description": ""},
        headers={"x-chatwoot-access-token": "agent-tok"},
    )
    assert res.status_code == 403


def test_administrator_can_create_role_and_assign(client):
    res = client.post(
        "/authz/roles",
        json={"id": "leader", "name": "Team Leader", "description": ""},
        headers={"x-chatwoot-access-token": "admin-tok"},
    )
    assert res.status_code == 200

    res = client.post(
        "/authz/roles/leader/assign",
        json={"chatwoot_user_id": 99},
        headers={"x-chatwoot-access-token": "admin-tok"},
    )
    assert res.status_code == 200
