import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.deps import require_permission
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults
from chatbot.platform.config import get_settings


def _app_with_endpoint(dep):
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(dep)])
    def protected():
        return {"ok": True}

    return TestClient(app)


def test_rbac_disabled_falls_back_to_shared_secret():
    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "faq_admin_api_key": "secret123"}
    )
    dep = require_permission("sla.manage", repo=None, validator=None, settings=settings)
    client = _app_with_endpoint(dep)

    assert client.get("/protected").status_code == 401
    assert client.get("/protected", headers={"x-api-key": "wrong"}).status_code == 401
    assert client.get("/protected", headers={"x-api-key": "secret123"}).status_code == 200


@pytest.mark.asyncio
async def test_rbac_enabled_allows_user_with_permission(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_test.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=7, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    validator = TokenValidator(settings)
    dep = require_permission("sla.manage", repo=repo, validator=validator, settings=settings)
    client = _app_with_endpoint(dep)

    res = client.get("/protected", headers={"x-chatwoot-access-token": "tok-abc"})
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_rbac_enabled_denies_user_without_permission(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_test2.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=8, role_id="agent")  # agent lacks sla.manage

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 8})
    )
    validator = TokenValidator(settings)
    dep = require_permission("sla.manage", repo=repo, validator=validator, settings=settings)
    client = _app_with_endpoint(dep)

    res = client.get("/protected", headers={"x-chatwoot-access-token": "tok-abc"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_rbac_enabled_missing_token_denies(tmp_path):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_test3.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    validator = TokenValidator(settings)
    dep = require_permission("sla.manage", repo=repo, validator=validator, settings=settings)
    client = _app_with_endpoint(dep)

    assert client.get("/protected").status_code == 401


@pytest.mark.asyncio
async def test_rbac_enabled_invalid_token_denies(tmp_path, respx_mock):
    """A present-but-bad token (Chatwoot rejects it) must resolve to a 401
    deny through require_permission's own wiring, not just TokenValidator's
    unit tests."""
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_test4.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(401, json={"error": "Invalid access token"})
    )
    validator = TokenValidator(settings)
    dep = require_permission("sla.manage", repo=repo, validator=validator, settings=settings)
    client = _app_with_endpoint(dep)

    res = client.get("/protected", headers={"x-chatwoot-access-token": "tok-bogus"})
    assert res.status_code == 401
